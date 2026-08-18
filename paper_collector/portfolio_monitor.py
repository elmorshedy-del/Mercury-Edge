from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg

import strategy_engines as se

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.environ["PAPER_SESSION_ID"]
INTERVAL_SECONDS = max(5, int(os.getenv("PAPER_RISK_INTERVAL_SECONDS", "10")))
STOP = False
ZERO = Decimal("0")
ONE = Decimal("1")


def stop(*_: object) -> None:
    global STOP
    STOP = True


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def global_config(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    row = conn.execute("SELECT config FROM paper_global_config WHERE id=1").fetchone()
    return dict(row[0]) if row and isinstance(row[0], dict) else {}


def settlement_results(conn: psycopg.Connection[Any]) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (event_ticker) event_ticker,raw_payload
          FROM settlement_rule_snapshots
         WHERE session_id=%s AND event_ticker IS NOT NULL
         ORDER BY event_ticker,captured_at DESC,id DESC
        """,
        (SESSION_ID,),
    ).fetchall()
    results: dict[str, str] = {}
    for _, raw_payload in rows:
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            ticker = str(market.get("ticker") or "")
            result = str(market.get("result") or "").lower()
            if ticker and result in ("yes", "no"):
                results[ticker] = result
    return results


def settle_open_positions(conn: psycopg.Connection[Any], results: dict[str, str]) -> int:
    rows = conn.execute(
        """
        SELECT id,portfolio_id,market_ticker,outcome_side,qty,cost_basis,fees_paid
          FROM paper_positions
         WHERE status='open'
         FOR UPDATE
        """
    ).fetchall()
    settled = 0
    for position_id, portfolio_id, ticker, side, qty, cost_basis, fees_paid in rows:
        result = results.get(str(ticker))
        if result not in ("yes", "no"):
            continue
        quantity = d(qty)
        payout = quantity if str(side) == result else ZERO
        realized = payout - d(cost_basis) - d(fees_paid)
        conn.execute(
            """
            UPDATE paper_positions
               SET status='settled',last_mark=%s,settlement_result=%s,
                   settled_payout=%s,realized_pnl=%s,settled_at=now(),updated_at=now()
             WHERE id=%s AND status='open'
            """,
            (ONE if str(side) == result else ZERO, result, payout, realized, position_id),
        )
        conn.execute(
            """
            UPDATE paper_portfolios
               SET cash_balance=cash_balance+%s,
                   realized_pnl=realized_pnl+%s,
                   updated_at=now()
             WHERE id=%s
            """,
            (payout, realized, portfolio_id),
        )
        settled += 1
    return settled


def settle_shadow_trials(conn: psycopg.Connection[Any], results: dict[str, str]) -> int:
    rows = conn.execute(
        """
        SELECT id,gross_cost,estimated_fee,details
          FROM paper_shadow_trials
         WHERE status='filled'
         ORDER BY id ASC
        """
    ).fetchall()
    settled = 0
    for trial_id, gross_cost, fee, details in rows:
        payload = details if isinstance(details, dict) else {}
        legs = payload.get("legs") if isinstance(payload.get("legs"), list) else []
        if not legs:
            continue
        payout = ZERO
        complete = True
        for leg in legs:
            if not isinstance(leg, dict):
                complete = False
                break
            ticker = str(leg.get("market_ticker") or "")
            side = str(leg.get("side") or "")
            qty = d(leg.get("qty") or 0)
            result = results.get(ticker)
            if result not in ("yes", "no"):
                complete = False
                break
            if side == result:
                payout += qty
        if not complete:
            continue
        realized = payout - d(gross_cost) - d(fee)
        conn.execute(
            """
            UPDATE paper_shadow_trials
               SET status='settled',settled_payout=%s,realized_pnl=%s,settled_at=now()
             WHERE id=%s AND status='filled'
            """,
            (payout, realized, trial_id),
        )
        settled += 1
    return settled


def mark_portfolio(
    conn: psycopg.Connection[Any],
    portfolio_id: int,
    starting_bankroll: Decimal,
    cash_balance: Decimal,
    mode_config: dict[str, Any],
    cfg: dict[str, Any],
    now_ms: int,
) -> tuple[Decimal, Decimal, Decimal, str, bool, list[str]]:
    positions = conn.execute(
        """
        SELECT id,market_ticker,outcome_side,qty,last_mark
          FROM paper_positions
         WHERE portfolio_id=%s AND status='open' AND qty>0
         ORDER BY id
        """,
        (portfolio_id,),
    ).fetchall()

    mark_value = ZERO
    missing: list[str] = []
    for position_id, ticker, side, qty, last_mark in positions:
        book = se.reconstruct_full_book(conn, SESSION_ID, str(ticker), now_ms)
        mark = se.best_bid(book, str(side)) if book else None
        if mark is None:
            missing.append(str(ticker))
            fallback = d(last_mark) if last_mark is not None else ZERO
            mark_value += fallback * d(qty)
            continue
        mark_value += mark * d(qty)
        conn.execute(
            "UPDATE paper_positions SET last_mark=%s,updated_at=now() WHERE id=%s",
            (mark, position_id),
        )

    equity = cash_balance + mark_value
    previous = conn.execute(
        "SELECT high_watermark FROM paper_risk_state WHERE portfolio_id=%s",
        (portfolio_id,),
    ).fetchone()
    previous_high = d(previous[0]) if previous else starting_bankroll
    high_water = max(starting_bankroll, previous_high, equity)
    drawdown = ZERO if high_water <= 0 else max(ZERO, (high_water - equity) / high_water)

    mark_complete = not missing
    policy = mode_config.get("drawdown_policy") if isinstance(mode_config.get("drawdown_policy"), dict) else {}
    caution = d(policy.get("caution", 0.05))
    reduced = d(policy.get("reduced", 0.10))
    pause = d(policy.get("pause", 0.20))
    caution_mult = d(policy.get("caution_multiplier", 0.75))
    reduced_mult = d(policy.get("reduced_multiplier", 0.50))

    if not mark_complete and bool(cfg.get("mark_incomplete_blocks_new_entries", True)):
        state, multiplier = "mark_incomplete", ZERO
    elif drawdown >= pause:
        state, multiplier = "paused", ZERO
    elif drawdown >= reduced:
        state, multiplier = "reduced", reduced_mult
    elif drawdown >= caution:
        state, multiplier = "caution", caution_mult
    else:
        state, multiplier = "normal", ONE

    return mark_value, equity, high_water, state, mark_complete, missing, drawdown, multiplier


def update_risk_states(conn: psycopg.Connection[Any], cfg: dict[str, Any]) -> int:
    rows = conn.execute(
        """
        SELECT p.id,p.starting_bankroll,p.cash_balance,c.config
          FROM paper_portfolios p
          JOIN paper_portfolio_mode_configs c ON c.mode_code=p.mode_code
         WHERE p.session_id=%s AND p.status='running'
         ORDER BY p.id
        """,
        (SESSION_ID,),
    ).fetchall()
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    updated = 0
    for portfolio_id, start, cash, mode_config in rows:
        config = dict(mode_config) if isinstance(mode_config, dict) else {}
        mark_value, equity, high_water, state, mark_complete, missing, drawdown, multiplier = mark_portfolio(
            conn,
            int(portfolio_id),
            d(start),
            d(cash),
            config,
            cfg,
            now_ms,
        )
        conn.execute(
            """
            INSERT INTO paper_risk_state(
              portfolio_id,high_watermark,equity,drawdown_pct,risk_multiplier,state,mark_complete,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT (portfolio_id) DO UPDATE SET
              high_watermark=EXCLUDED.high_watermark,
              equity=EXCLUDED.equity,
              drawdown_pct=EXCLUDED.drawdown_pct,
              risk_multiplier=EXCLUDED.risk_multiplier,
              state=EXCLUDED.state,
              mark_complete=EXCLUDED.mark_complete,
              updated_at=now()
            """,
            (portfolio_id, high_water, equity, drawdown, multiplier, state, mark_complete),
        )
        conn.execute(
            """
            INSERT INTO paper_equity_snapshots(
              portfolio_id,captured_at,cash_balance,open_mark_value,equity,high_watermark,
              drawdown_pct,risk_multiplier,risk_state,mark_complete,details
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            """,
            (
                portfolio_id,
                now,
                d(cash),
                mark_value,
                equity,
                high_water,
                drawdown,
                multiplier,
                state,
                mark_complete,
                json.dumps({"missing_marks": missing}, separators=(",", ":")),
            ),
        )
        updated += 1
    return updated


def cycle(conn: psycopg.Connection[Any]) -> dict[str, int]:
    cfg = global_config(conn)
    results = settlement_results(conn)
    with conn.transaction():
        positions = settle_open_positions(conn, results) if bool(cfg.get("settlement_cash_recycling", True)) else 0
        trials = settle_shadow_trials(conn, results)
        portfolios = update_risk_states(conn, cfg)
    return {"settled_positions": positions, "settled_trials": trials, "risk_states": portfolios}


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"event": "paper_portfolio_monitor_start", "session_id": SESSION_ID, "interval_seconds": INTERVAL_SECONDS}))
    with psycopg.connect(DATABASE_URL) as conn:
        while not STOP:
            started = time.monotonic()
            try:
                summary = cycle(conn)
                if summary["settled_positions"] or summary["settled_trials"]:
                    print(json.dumps({"event": "paper_settlement_update", **summary}, separators=(",", ":")), flush=True)
            except Exception as exc:
                conn.rollback()
                print(json.dumps({"event": "paper_portfolio_monitor_error", "error": repr(exc)}), flush=True)
            remaining = max(0.1, INTERVAL_SECONDS - (time.monotonic() - started))
            deadline = time.monotonic() + remaining
            while not STOP and time.monotonic() < deadline:
                time.sleep(min(0.5, deadline - time.monotonic()))
    print(json.dumps({"event": "paper_portfolio_monitor_stop", "session_id": SESSION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
