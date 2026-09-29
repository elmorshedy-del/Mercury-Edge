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
from stations import STATIONS

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.environ["PAPER_SESSION_ID"]
INTERVAL_SECONDS = max(5, int(os.getenv("PAPER_EXPERIMENT_EVAL_SECONDS", "10")))
DEFAULT_HORIZONS_MS = [1000, 5000, 15000, 30000, 60000, 120000, 300000, 600000, 1800000]
STOP = False
ZERO = Decimal("0")


def stop(*_: object) -> None:
    global STOP
    STOP = True


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def global_config(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    row = conn.execute("SELECT config FROM paper_global_config WHERE id=1").fetchone()
    return dict(row[0]) if row and isinstance(row[0], dict) else {}


def horizons(cfg: dict[str, Any]) -> list[int]:
    raw = cfg.get("experiment_exit_horizons_ms") or DEFAULT_HORIZONS_MS
    return sorted({max(0, int(value)) for value in raw})


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def market_open_at(conn: psycopg.Connection[Any], ticker: str, epoch_ms: int) -> bool:
    row = conn.execute(
        "SELECT raw_payload FROM paper_market_results WHERE market_ticker=%s",
        (ticker,),
    ).fetchone()
    if not row or not isinstance(row[0], dict):
        return False
    payload = row[0]
    opened = parse_time(payload.get("open_time"))
    closed = parse_time(payload.get("close_time"))
    at = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return (opened is None or at >= opened) and (closed is None or at < closed)


def sell_levels(book: se.FullBook, side: str, qty: Decimal) -> list[tuple[Decimal, Decimal]] | None:
    source = book.yes_bids if side == "yes" else book.no_bids if side == "no" else None
    if source is None:
        return None
    remaining = qty
    fills: list[tuple[Decimal, Decimal]] = []
    for price in sorted(source, reverse=True):
        available = source[price]
        take = min(available, remaining)
        if take > 0:
            fills.append((price, take))
            remaining -= take
        if remaining <= 0:
            return fills
    return None


def fee_multiplier_for_trial(
    conn: psycopg.Connection[Any],
    station: str,
    triggered_at: datetime,
) -> Decimal | None:
    meta = STATIONS.get(station)
    if not meta:
        return None
    rules = se.series_rules_before(conn, meta["series"], triggered_at)
    if not rules or rules.get("fee_multiplier") is None:
        return None
    return d(rules["fee_multiplier"])


def evaluate_trial_horizon(
    conn: psycopg.Connection[Any],
    trial: tuple[Any, ...],
    horizon_ms: int,
    now_ms: int,
) -> bool:
    trial_id, gross_cost, entry_fee, details, station, triggered_at = trial
    payload = details if isinstance(details, dict) else {}
    legs = payload.get("legs") if isinstance(payload.get("legs"), list) else []
    if not legs:
        return False
    arrival_times = [int(leg.get("arrival_ms")) for leg in legs if isinstance(leg, dict) and leg.get("arrival_ms") is not None]
    if len(arrival_times) != len(legs):
        return False
    mark_ms = max(arrival_times) + horizon_ms
    if mark_ms > now_ms:
        return False

    multiplier = fee_multiplier_for_trial(conn, str(station), triggered_at)
    if multiplier is None:
        details_out = {"reason": "MISSING_FEE_MODEL_AT_EXIT_HORIZON"}
        conn.execute(
            """
            INSERT INTO paper_shadow_trial_horizons(
              trial_id,horizon_ms,mark_epoch_ms,executable,details
            ) VALUES (%s,%s,%s,false,%s::jsonb)
            ON CONFLICT (trial_id,horizon_ms) DO NOTHING
            """,
            (trial_id, horizon_ms, mark_ms, json.dumps(details_out, separators=(",", ":"))),
        )
        return True

    exit_value = ZERO
    exit_fee = ZERO
    exit_legs: list[dict[str, Any]] = []
    executable = True
    reason: str | None = None

    for leg in legs:
        ticker = str(leg.get("market_ticker") or "")
        side = str(leg.get("side") or "")
        qty = d(leg.get("qty") or 0)
        if not ticker or side not in ("yes", "no") or qty <= 0:
            executable = False
            reason = "INVALID_TRIAL_LEG"
            break
        if not market_open_at(conn, ticker, mark_ms):
            executable = False
            reason = "MARKET_NOT_TRADABLE_AT_HORIZON"
            break
        book = se.reconstruct_full_book(conn, SESSION_ID, ticker, mark_ms)
        if not book:
            executable = False
            reason = "NO_VALID_L2_AT_EXIT_HORIZON"
            break
        fills = sell_levels(book, side, qty)
        if not fills:
            executable = False
            reason = "INSUFFICIENT_EXIT_DEPTH"
            break
        leg_value = sum((price * fill_qty for price, fill_qty in fills), ZERO)
        leg_fee, _ = se.fee_for_fills(fills, multiplier)
        exit_value += leg_value
        exit_fee += leg_fee
        exit_legs.append({
            "market_ticker": ticker,
            "side": side,
            "qty": format(qty, "f"),
            "gross_exit_value": format(leg_value, "f"),
            "exit_fee": format(leg_fee, "f"),
            "fills": [[format(price, "f"), format(fill_qty, "f")] for price, fill_qty in fills],
            "book_seq": book.last_seq,
            "connection_id": book.connection_id,
        })

    pnl = exit_value - exit_fee - d(gross_cost) - d(entry_fee) if executable else None
    conn.execute(
        """
        INSERT INTO paper_shadow_trial_horizons(
          trial_id,horizon_ms,mark_epoch_ms,exit_value,exit_fee,exit_pnl,executable,details
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        ON CONFLICT (trial_id,horizon_ms) DO NOTHING
        """,
        (
            trial_id,
            horizon_ms,
            mark_ms,
            exit_value if executable else None,
            exit_fee if executable else None,
            pnl,
            executable,
            json.dumps({"reason": reason, "legs": exit_legs}, separators=(",", ":")),
        ),
    )
    return True


def evaluate_pending(conn: psycopg.Connection[Any]) -> int:
    cfg = global_config(conn)
    target_horizons = horizons(cfg)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    trials = conn.execute(
        """
        SELECT t.id,t.gross_cost,t.estimated_fee,t.details,s.station_code,s.triggered_at
          FROM paper_shadow_trials t
          JOIN paper_signals s ON s.id=t.signal_id
         WHERE t.session_id=%s
           AND t.status IN ('filled','settled')
         ORDER BY t.id DESC
         LIMIT 3000
        """,
        (SESSION_ID,),
    ).fetchall()
    written = 0
    for trial in trials:
        existing = {
            int(row[0])
            for row in conn.execute(
                "SELECT horizon_ms FROM paper_shadow_trial_horizons WHERE trial_id=%s",
                (trial[0],),
            ).fetchall()
        }
        for horizon_ms in target_horizons:
            if horizon_ms in existing:
                continue
            if evaluate_trial_horizon(conn, trial, horizon_ms, now_ms):
                written += 1
    conn.commit()
    return written


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({
        "event": "paper_experiment_evaluator_start",
        "session_id": SESSION_ID,
        "interval_seconds": INTERVAL_SECONDS,
    }))
    with psycopg.connect(DATABASE_URL) as conn:
        while not STOP:
            started = time.monotonic()
            try:
                count = evaluate_pending(conn)
                if count:
                    print(json.dumps({"event": "paper_experiment_horizons", "written": count}), flush=True)
            except Exception as exc:
                conn.rollback()
                print(json.dumps({"event": "paper_experiment_evaluator_error", "error": repr(exc)}), flush=True)
            remaining = max(0.1, INTERVAL_SECONDS - (time.monotonic() - started))
            deadline = time.monotonic() + remaining
            while not STOP and time.monotonic() < deadline:
                time.sleep(min(0.5, deadline - time.monotonic()))
    print(json.dumps({"event": "paper_experiment_evaluator_stop", "session_id": SESSION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
