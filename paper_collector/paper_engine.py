from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

import psycopg

from stations import STATIONS

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.environ["PAPER_SESSION_ID"]
MODEL_VERSION = os.getenv("PAPER_MODEL_VERSION", "paper-v1.2")
ENGINE_VERSION = os.getenv("PAPER_EXECUTION_MODEL_VERSION", "python-parallel-portfolios-v1")
POLL_MS = max(50, int(os.getenv("PAPER_ENGINE_POLL_MS", "250")))
DEFAULT_PROCESS_DELAY_MS = max(1000, int(os.getenv("PAPER_ENGINE_PROCESS_DELAY_MS", "6000")))
ONE = Decimal("1")
CENT = Decimal("0.01")
TEN_THOUSANDTH = Decimal("0.0001")
QTY_STEP = Decimal("0.01")
STOP = False


@dataclass
class BookState:
    yes_bids: dict[Decimal, Decimal]
    connection_id: str
    last_seq: int | None
    snapshot_id: int
    snapshot_received_ms: int


@dataclass
class Candidate:
    signal_id: int
    station: str
    region: str
    event_ticker: str
    market_ticker: str
    trigger_at: datetime
    trigger_epoch_ms: int
    confirmed_high_f: Decimal
    upper_bound_f: Decimal
    fee_type: str
    fee_multiplier: Decimal
    evidence: dict[str, Any]


@dataclass
class EvaluatedCandidate:
    candidate: Candidate
    book: BookState
    asks: list[tuple[Decimal, Decimal]]
    best_ask: Decimal
    depth_notional: Decimal
    roi: Decimal
    edge: Decimal
    latency_ms: int
    max_price: Decimal


def stop(*_: object) -> None:
    global STOP
    STOP = True


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=json_default).encode()
    return hashlib.sha256(raw).hexdigest()


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def active_at(market: dict[str, Any], when: datetime) -> bool:
    opened = parse_time(market.get("open_time"))
    closed = parse_time(market.get("close_time") or market.get("expected_expiration_time"))
    if opened and when < opened:
        return False
    if closed and when > closed:
        return False
    return True


def market_upper_bound(market: dict[str, Any]) -> Decimal | None:
    raw = market.get("cap_strike")
    if raw is None:
        return None
    try:
        return d(raw)
    except Exception:
        return None


def ensure_session_and_portfolios(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        """
        INSERT INTO paper_sessions(id, mode, model_version, status, config)
        VALUES (%s,'paper_live',%s,'running','{}'::jsonb)
        ON CONFLICT (id) DO UPDATE SET model_version=EXCLUDED.model_version,status='running'
        """,
        (SESSION_ID, MODEL_VERSION),
    )
    conn.execute(
        """
        INSERT INTO paper_portfolios(session_id,mode_code,starting_bankroll,cash_balance,status)
        SELECT %s, mode_code, starting_bankroll, starting_bankroll, 'running'
          FROM paper_portfolio_mode_configs
         WHERE enabled
        ON CONFLICT (session_id,mode_code) DO NOTHING
        """,
        (SESSION_ID,),
    )
    conn.execute(
        """
        INSERT INTO paper_engine_state(session_id,component,last_weather_id)
        VALUES (%s,'paper_engine',0)
        ON CONFLICT (session_id,component) DO NOTHING
        """,
        (SESSION_ID,),
    )
    conn.commit()


def load_global(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    row = conn.execute("SELECT config FROM paper_global_config WHERE id=1").fetchone()
    return dict(row[0]) if row and isinstance(row[0], dict) else {}


def load_modes(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.mode_code,c.display_name,c.enabled,c.starting_bankroll,c.config,
               p.id,p.starting_bankroll,p.cash_balance,p.reserved_cash,p.status
          FROM paper_portfolio_mode_configs c
          JOIN paper_portfolios p ON p.session_id=%s AND p.mode_code=c.mode_code
         WHERE c.enabled AND p.status='running'
         ORDER BY c.mode_code
        """,
        (SESSION_ID,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append({
            "mode_code": str(row[0]),
            "display_name": str(row[1]),
            "enabled": bool(row[2]),
            "configured_start": d(row[3]),
            "config": dict(row[4]) if isinstance(row[4], dict) else {},
            "portfolio_id": int(row[5]),
            "starting_bankroll": d(row[6]),
            "cash_balance": d(row[7]),
            "reserved_cash": d(row[8]),
            "status": str(row[9]),
        })
    return result


def dbn_strategy(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT enabled,paper_trade_enabled,shadow_enabled,config
          FROM paper_strategy_configs WHERE strategy_code='DBN'
        """
    ).fetchone()
    if not row or not bool(row[0]):
        return None
    return {
        "paper_trade_enabled": bool(row[1]),
        "shadow_enabled": bool(row[2]),
        "config": dict(row[3]) if isinstance(row[3], dict) else {},
    }


def compatibility_is_proven(conn: psycopg.Connection[Any], weather: dict[str, Any]) -> bool:
    if weather["compatibility_status"] != "proven" or not weather["compatibility_rule"]:
        return False
    row = conn.execute(
        "SELECT status FROM source_compatibility_rules WHERE compatibility_key=%s",
        (weather["compatibility_rule"],),
    ).fetchone()
    return bool(row and str(row[0]) == "proven")


def series_rules_before(conn: psycopg.Connection[Any], series: str, when: datetime) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT rules_hash,fee_type,fee_multiplier,settlement_sources,captured_at
          FROM settlement_rule_snapshots
         WHERE series_ticker=%s AND event_ticker IS NULL AND captured_at<=%s
         ORDER BY captured_at DESC,id DESC LIMIT 1
        """,
        (series, when),
    ).fetchone()
    if not row:
        return None
    return {
        "rules_hash": str(row[0]),
        "fee_type": str(row[1]) if row[1] is not None else None,
        "fee_multiplier": d(row[2]) if row[2] is not None else None,
        "settlement_sources": row[3],
        "captured_at": row[4],
    }


def event_rule_candidates(conn: psycopg.Connection[Any], series: str, when: datetime) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT event_ticker,rules_hash,raw_payload,captured_at
          FROM settlement_rule_snapshots
         WHERE series_ticker=%s AND event_ticker IS NOT NULL AND captured_at<=%s
         ORDER BY captured_at DESC,id DESC
         LIMIT 40
        """,
        (series, when),
    ).fetchall()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event_ticker, rules_hash, raw_payload, captured_at in rows:
        ticker = str(event_ticker)
        if ticker in seen:
            continue
        seen.add(ticker)
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        markets = event.get("markets") if isinstance(event.get("markets"), list) else []
        live_markets = [m for m in markets if isinstance(m, dict) and active_at(m, when)]
        if not live_markets:
            continue
        result.append({
            "event_ticker": ticker,
            "rules_hash": str(rules_hash),
            "captured_at": captured_at,
            "markets": live_markets,
        })
    return result


def insert_signal(
    conn: psycopg.Connection[Any],
    *,
    weather: dict[str, Any],
    event_ticker: str,
    market: dict[str, Any],
    upper_bound: Decimal,
    confirmed_high: Decimal,
    event_rules: dict[str, Any],
    series_rules: dict[str, Any] | None,
    proven: bool,
) -> tuple[int, str]:
    market_ticker = str(market.get("ticker") or "")
    prerequisites: list[str] = []
    if not proven:
        prerequisites.append("SETTLEMENT_COMPATIBILITY_UNVERIFIED")
    if not series_rules:
        prerequisites.append("MISSING_PRIOR_SERIES_RULE_SNAPSHOT")
    elif series_rules.get("fee_type") not in ("quadratic", "quadratic_with_maker_fees"):
        prerequisites.append("UNSUPPORTED_OR_MISSING_FEE_MODEL")
    if not event_rules:
        prerequisites.append("MISSING_PRIOR_EVENT_RULE_SNAPSHOT")

    status = "approved" if not prerequisites else "shadow_only"
    evidence = {
        "weather_event_id": weather["id"],
        "source": weather["source"],
        "report_type": weather["report_type"],
        "observed_at": weather["observed_at"],
        "first_seen_at": weather["first_seen_at"],
        "compatibility_status": weather["compatibility_status"],
        "compatibility_rule": weather["compatibility_rule"],
        "confirmed_high_f": confirmed_high,
        "market": {
            "ticker": market_ticker,
            "title": market.get("title"),
            "subtitle": market.get("subtitle"),
            "floor_strike": market.get("floor_strike"),
            "cap_strike": market.get("cap_strike"),
            "strike_type": market.get("strike_type"),
        },
        "upper_bound_f": upper_bound,
        "event_rules_hash": event_rules.get("rules_hash"),
        "series_rules_hash": series_rules.get("rules_hash") if series_rules else None,
        "auditor_prerequisites": prerequisites,
    }
    state_hash = canonical_hash({
        "session": SESSION_ID,
        "strategy": "DBN",
        "weather_event_id": weather["id"],
        "market_ticker": market_ticker,
        "confirmed_high_f": confirmed_high,
        "upper_bound_f": upper_bound,
        "event_rules_hash": event_rules.get("rules_hash"),
        "series_rules_hash": series_rules.get("rules_hash") if series_rules else None,
        "compatibility_rule": weather["compatibility_rule"],
    })
    row = conn.execute(
        """
        INSERT INTO paper_signals(
          session_id,station_code,event_ticker,strategy_code,signal_class,
          triggered_at,trigger_epoch_ms,trigger_epoch_ns,weather_event_id,
          state_hash,evidence,auditor_status,blocked_reason
        ) VALUES (%s,%s,%s,'DBN','hard_state',%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
        ON CONFLICT (session_id,strategy_code,state_hash)
        DO UPDATE SET evidence=EXCLUDED.evidence,auditor_status=EXCLUDED.auditor_status,blocked_reason=EXCLUDED.blocked_reason
        RETURNING id,auditor_status
        """,
        (
            SESSION_ID,
            weather["station_code"],
            event_ticker,
            weather["first_seen_at"],
            weather["received_epoch_ms"],
            str(weather["received_epoch_ns"]),
            weather["id"],
            state_hash,
            json.dumps(evidence, separators=(",", ":"), default=json_default),
            status,
            ",".join(prerequisites) if prerequisites else None,
        ),
    ).fetchone()
    if not row:
        raise RuntimeError("signal insert failed")
    return int(row[0]), str(row[1])


def reconstruct_book(conn: psycopg.Connection[Any], market_ticker: str, arrival_ms: int) -> BookState | None:
    snapshot = conn.execute(
        """
        SELECT id,connection_id::text,seq,received_epoch_ms,raw_text
          FROM market_data_journal
         WHERE session_id=%s AND market_ticker=%s
           AND channel='orderbook_snapshot' AND received_epoch_ms<=%s
         ORDER BY received_epoch_ms DESC,id DESC LIMIT 1
        """,
        (SESSION_ID, market_ticker, arrival_ms),
    ).fetchone()
    if not snapshot or not snapshot[1]:
        return None
    snapshot_id = int(snapshot[0])
    connection_id = str(snapshot[1])

    fatal = conn.execute(
        """
        SELECT 1 FROM audit_findings
         WHERE session_id=%s AND severity='fatal'
           AND details->>'connection_id'=%s
           AND detected_at<=to_timestamp(%s/1000.0)
         LIMIT 1
        """,
        (SESSION_ID, connection_id, arrival_ms),
    ).fetchone()
    if fatal:
        return None

    rows = conn.execute(
        """
        SELECT id,channel,seq,received_epoch_ms,raw_text
          FROM market_data_journal
         WHERE session_id=%s AND connection_id=%s::uuid AND market_ticker=%s
           AND id>=%s AND received_epoch_ms<=%s
           AND channel IN ('orderbook_snapshot','orderbook_delta')
         ORDER BY id ASC
        """,
        (SESSION_ID, connection_id, market_ticker, snapshot_id, arrival_ms),
    ).fetchall()
    yes: dict[Decimal, Decimal] = {}
    last_seq: int | None = None
    seen_snapshot = False
    for row_id, channel, seq, received_ms, raw_text in rows:
        data = json.loads(str(raw_text))
        msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
        if channel == "orderbook_snapshot":
            yes.clear()
            for level in msg.get("yes_dollars_fp") or []:
                if not isinstance(level, list) or len(level) < 2:
                    continue
                price, qty = d(level[0]), d(level[1])
                if qty > 0:
                    yes[price] = qty
            seen_snapshot = True
        elif channel == "orderbook_delta" and seen_snapshot and str(msg.get("side")) == "yes":
            price = d(msg.get("price_dollars"))
            delta = d(msg.get("delta_fp"))
            next_qty = yes.get(price, Decimal(0)) + delta
            if next_qty < 0:
                return None
            if next_qty == 0:
                yes.pop(price, None)
            else:
                yes[price] = next_qty
        if seq is not None:
            last_seq = int(seq)
    if not seen_snapshot:
        return None
    return BookState(
        yes_bids=yes,
        connection_id=connection_id,
        last_seq=last_seq,
        snapshot_id=snapshot_id,
        snapshot_received_ms=int(snapshot[3]),
    )


def no_asks(book: BookState, max_price: Decimal) -> list[tuple[Decimal, Decimal]]:
    asks: list[tuple[Decimal, Decimal]] = []
    for yes_price, qty in book.yes_bids.items():
        no_price = ONE - yes_price
        if qty > 0 and Decimal(0) <= no_price <= max_price:
            asks.append((no_price, qty))
    asks.sort(key=lambda item: item[0])
    return asks


def ceil_increment(value: Decimal, increment: Decimal) -> Decimal:
    if value <= 0:
        return Decimal(0)
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def fee_for_fills(fills: list[tuple[Decimal, Decimal]], multiplier: Decimal) -> tuple[Decimal, list[dict[str, str]]]:
    accumulator = Decimal(0)
    total = Decimal(0)
    details: list[dict[str, str]] = []
    for price, qty in fills:
        raw_trade_fee = multiplier * Decimal("0.07") * qty * price * (ONE - price)
        trade_fee = ceil_increment(raw_trade_fee, TEN_THOUSANDTH)
        notional = price * qty
        balance_change = -notional - trade_fee
        floored = (balance_change / CENT).to_integral_value(rounding=ROUND_FLOOR) * CENT
        rounding_fee = balance_change - floored
        accumulator += rounding_fee
        rebate = (accumulator / CENT).to_integral_value(rounding=ROUND_FLOOR) * CENT
        accumulator -= rebate
        net_fee = max(Decimal(0), trade_fee + rounding_fee - rebate)
        total += net_fee
        details.append({
            "price": format(price, "f"),
            "qty": format(qty, "f"),
            "trade_fee": format(trade_fee, "f"),
            "rounding_fee": format(rounding_fee, "f"),
            "rebate": format(rebate, "f"),
            "net_fee": format(net_fee, "f"),
            "accumulator": format(accumulator, "f"),
        })
    return total, details


def total_cost(fills: list[tuple[Decimal, Decimal]], multiplier: Decimal) -> tuple[Decimal, Decimal, list[dict[str, str]]]:
    gross = sum((price * qty for price, qty in fills), Decimal(0))
    fee, breakdown = fee_for_fills(fills, multiplier)
    return gross + fee, fee, breakdown


def fills_for_budget(
    asks: list[tuple[Decimal, Decimal]],
    budget: Decimal,
    multiplier: Decimal,
) -> tuple[list[tuple[Decimal, Decimal]], Decimal, Decimal, list[dict[str, str]]]:
    if budget <= 0:
        return [], Decimal(0), Decimal(0), []
    fills: list[tuple[Decimal, Decimal]] = []
    for price, available_qty in asks:
        high_fp = int((available_qty / QTY_STEP).to_integral_value(rounding=ROUND_FLOOR))
        if high_fp <= 0:
            continue
        low, high = 0, high_fp
        best = 0
        while low <= high:
            mid = (low + high) // 2
            trial = fills + ([(price, QTY_STEP * mid)] if mid > 0 else [])
            cost, _, _ = total_cost(trial, multiplier)
            if cost <= budget:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        if best > 0:
            fills.append((price, QTY_STEP * best))
        cost, _, _ = total_cost(fills, multiplier)
        if budget - cost < price * QTY_STEP:
            break
    cost, fee, breakdown = total_cost(fills, multiplier)
    return fills, cost, fee, breakdown


def exposure(
    conn: psycopg.Connection[Any],
    portfolio_id: int,
    event_ticker: str,
    region_stations: list[str],
    when: datetime,
    day_timezone: str,
) -> tuple[Decimal, Decimal, Decimal]:
    event_row = conn.execute(
        """
        SELECT COALESCE(sum(o.total_cost_micros),0)
          FROM paper_orders o JOIN paper_signals s ON s.id=o.signal_id
         WHERE o.portfolio_id=%s AND o.status IN ('filled','partial') AND s.event_ticker=%s
        """,
        (portfolio_id, event_ticker),
    ).fetchone()
    region_row = conn.execute(
        """
        SELECT COALESCE(sum(o.total_cost_micros),0)
          FROM paper_orders o JOIN paper_signals s ON s.id=o.signal_id
         WHERE o.portfolio_id=%s AND o.status IN ('filled','partial') AND s.station_code=ANY(%s)
        """,
        (portfolio_id, region_stations),
    ).fetchone()
    day_row = conn.execute(
        """
        SELECT COALESCE(sum(total_cost_micros),0)
          FROM paper_orders
         WHERE portfolio_id=%s AND status IN ('filled','partial')
           AND (decision_at AT TIME ZONE %s)::date=(%s AT TIME ZONE %s)::date
        """,
        (portfolio_id, day_timezone, when, day_timezone),
    ).fetchone()
    return (
        d(event_row[0]) / Decimal(1_000_000),
        d(region_row[0]) / Decimal(1_000_000),
        d(day_row[0]) / Decimal(1_000_000),
    )


def record_decision(
    conn: psycopg.Connection[Any],
    portfolio_id: int,
    signal_id: int,
    decision: str,
    budget: Decimal,
    reason: str,
    details: dict[str, Any],
    score: Decimal | None = None,
) -> bool:
    row = conn.execute(
        """
        INSERT INTO paper_portfolio_decisions(portfolio_id,signal_id,decision,requested_budget,score,reason,details)
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
        ON CONFLICT (portfolio_id,signal_id) DO NOTHING
        RETURNING id
        """,
        (
            portfolio_id,
            signal_id,
            decision,
            budget,
            score,
            reason,
            json.dumps(details, separators=(",", ":"), default=json_default),
        ),
    ).fetchone()
    return bool(row)


def place_order(
    conn: psycopg.Connection[Any],
    mode: dict[str, Any],
    evaluated: EvaluatedCandidate,
    budget: Decimal,
) -> Decimal:
    candidate = evaluated.candidate
    portfolio_id = int(mode["portfolio_id"])
    existing = conn.execute(
        "SELECT 1 FROM paper_portfolio_decisions WHERE portfolio_id=%s AND signal_id=%s",
        (portfolio_id, candidate.signal_id),
    ).fetchone()
    if existing:
        return Decimal(0)

    fills, cost, fee, fee_breakdown = fills_for_budget(
        evaluated.asks,
        max(Decimal(0), budget),
        candidate.fee_multiplier,
    )
    if not fills or cost <= 0:
        record_decision(
            conn, portfolio_id, candidate.signal_id, "skip", budget,
            "NO_EXECUTABLE_DEPTH_WITHIN_BUDGET",
            {"max_price": evaluated.max_price, "best_ask": evaluated.best_ask},
            evaluated.roi,
        )
        return Decimal(0)

    qty = sum((q for _, q in fills), Decimal(0))
    gross = sum((p * q for p, q in fills), Decimal(0))
    avg = gross / qty
    worst = max(p for p, _ in fills)
    arrival_ms = candidate.trigger_epoch_ms + evaluated.latency_ms
    arrival_at = datetime.fromtimestamp(arrival_ms / 1000, tz=timezone.utc)
    requested_qty_fp = int((qty / QTY_STEP).to_integral_value())
    max_price_fp = int((evaluated.max_price * Decimal(10000)).to_integral_value())
    gross_micros = int((gross * Decimal(1_000_000)).to_integral_value())
    fee_micros = int((fee * Decimal(1_000_000)).to_integral_value())
    cost_micros = int((cost * Decimal(1_000_000)).to_integral_value())

    if not record_decision(
        conn, portfolio_id, candidate.signal_id, "trade", budget, "EXECUTABLE_DBN",
        {
            "mode": mode["mode_code"],
            "allocation": mode["config"].get("allocation"),
            "max_price": evaluated.max_price,
            "best_ask": evaluated.best_ask,
            "depth_notional": evaluated.depth_notional,
            "connection_id": evaluated.book.connection_id,
            "region": candidate.region,
        },
        evaluated.roi,
    ):
        return Decimal(0)

    order = conn.execute(
        """
        INSERT INTO paper_orders(
          session_id,signal_id,portfolio_id,latency_profile_ms,strategy_code,market_ticker,outcome_side,
          requested_qty,decision_at,simulated_arrival_at,book_seq,status,avg_fill_price,filled_qty,
          gross_cost,estimated_fee,worst_price,book_snapshot,audit,
          requested_qty_fp,max_price_fp,filled_qty_fp,gross_cost_micros,fee_micros,total_cost_micros,
          state_known_through_epoch_ms,execution_model_version,fee_breakdown
        ) VALUES (
          %s,%s,%s,%s,'DBN',%s,'no',%s,%s,%s,%s,'filled',%s,%s,
          %s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
        ) RETURNING id
        """,
        (
            SESSION_ID,
            candidate.signal_id,
            portfolio_id,
            evaluated.latency_ms,
            candidate.market_ticker,
            qty,
            candidate.trigger_at,
            arrival_at,
            evaluated.book.last_seq,
            avg,
            qty,
            gross,
            fee,
            worst,
            json.dumps({
                "connection_id": evaluated.book.connection_id,
                "snapshot_id": evaluated.book.snapshot_id,
                "snapshot_received_ms": evaluated.book.snapshot_received_ms,
                "arrival_ms": arrival_ms,
                "asks_used": [[format(p, "f"), format(q, "f")] for p, q in fills],
            }, separators=(",", ":")),
            json.dumps(candidate.evidence, separators=(",", ":"), default=json_default),
            requested_qty_fp,
            max_price_fp,
            requested_qty_fp,
            gross_micros,
            fee_micros,
            cost_micros,
            arrival_ms,
            ENGINE_VERSION,
            json.dumps(fee_breakdown, separators=(",", ":")),
        ),
    ).fetchone()
    order_id = int(order[0])
    for index, (price, fill_qty) in enumerate(fills):
        level_fee = d(fee_breakdown[index]["net_fee"]) if index < len(fee_breakdown) else Decimal(0)
        conn.execute(
            """
            INSERT INTO paper_fills(order_id,price,qty,level_index,liquidity,estimated_fee)
            VALUES (%s,%s,%s,%s,'taker',%s)
            """,
            (order_id, price, fill_qty, index, level_fee),
        )
    conn.execute(
        """
        INSERT INTO paper_positions(portfolio_id,market_ticker,outcome_side,qty,cost_basis,fees_paid,status,updated_at)
        VALUES (%s,%s,'no',%s,%s,%s,'open',now())
        ON CONFLICT (portfolio_id,market_ticker,outcome_side)
        DO UPDATE SET qty=paper_positions.qty+EXCLUDED.qty,
                      cost_basis=paper_positions.cost_basis+EXCLUDED.cost_basis,
                      fees_paid=paper_positions.fees_paid+EXCLUDED.fees_paid,
                      status='open',updated_at=now()
        """,
        (portfolio_id, candidate.market_ticker, qty, gross, fee),
    )
    conn.execute(
        """
        UPDATE paper_portfolios
           SET cash_balance=cash_balance-%s,updated_at=now()
         WHERE id=%s AND cash_balance>=%s
        """,
        (cost, portfolio_id, cost),
    )
    return cost


def evaluate_for_mode(
    conn: psycopg.Connection[Any],
    candidate: Candidate,
    mode: dict[str, Any],
) -> EvaluatedCandidate | None:
    cfg = mode["config"]
    latency_ms = max(0, int(cfg.get("execution_latency_ms", 100)))
    max_price = d(cfg.get("max_no_price", "0.93"))
    arrival_ms = candidate.trigger_epoch_ms + latency_ms
    book = reconstruct_book(conn, candidate.market_ticker, arrival_ms)
    if not book:
        return None
    asks = no_asks(book, max_price)
    if not asks:
        return None
    best = asks[0][0]
    depth = sum((price * qty for price, qty in asks), Decimal(0))
    edge = ONE - best
    roi = edge / best if best > 0 else Decimal("999999")
    return EvaluatedCandidate(candidate, book, asks, best, depth, roi, edge, latency_ms, max_price)


def mode_budget(
    conn: psycopg.Connection[Any],
    mode: dict[str, Any],
    candidate: Candidate,
    global_cfg: dict[str, Any],
) -> Decimal:
    cfg = mode["config"]
    portfolio = conn.execute(
        "SELECT starting_bankroll,cash_balance FROM paper_portfolios WHERE id=%s FOR UPDATE",
        (mode["portfolio_id"],),
    ).fetchone()
    start = d(portfolio[0])
    cash = d(portfolio[1])
    reserve_floor = start * d(cfg.get("reserve_pct", 0))
    usable_cash = max(Decimal(0), cash - reserve_floor)
    trade_cap = start * d(cfg.get("max_trade_pct", 1))
    event_cap = start * d(cfg.get("max_event_pct", 1))
    region_cap = start * d(cfg.get("max_region_pct", 1))
    day_cap = start * d(cfg.get("max_daily_deployed_pct", 1))
    region_stations = [code for code, meta in STATIONS.items() if meta.get("region") == candidate.region]
    event_used, region_used, day_used = exposure(
        conn,
        int(mode["portfolio_id"]),
        candidate.event_ticker,
        region_stations,
        candidate.trigger_at,
        str(global_cfg.get("portfolio_day_timezone", "America/New_York")),
    )
    return max(Decimal(0), min(
        usable_cash,
        trade_cap,
        max(Decimal(0), event_cap - event_used),
        max(Decimal(0), region_cap - region_used),
        max(Decimal(0), day_cap - day_used),
    ))


def execute_candidates(
    conn: psycopg.Connection[Any],
    candidates: list[Candidate],
    modes: list[dict[str, Any]],
    global_cfg: dict[str, Any],
) -> None:
    if not candidates:
        return
    for mode in modes:
        evaluated: list[EvaluatedCandidate] = []
        for candidate in candidates:
            item = evaluate_for_mode(conn, candidate, mode)
            if item:
                evaluated.append(item)
            else:
                record_decision(
                    conn,
                    int(mode["portfolio_id"]),
                    candidate.signal_id,
                    "blocked",
                    Decimal(0),
                    "NO_VALID_L2_AT_SIMULATED_ARRIVAL",
                    {"mode": mode["mode_code"], "latency_ms": int(mode["config"].get("execution_latency_ms", 100))},
                )
        if not evaluated:
            continue

        allocation = str(mode["config"].get("allocation", "edge_weighted"))
        if allocation == "depth_first":
            evaluated.sort(key=lambda x: (x.depth_notional, x.roi), reverse=True)
        elif allocation in ("best_edge_first", "fill_available_capital"):
            evaluated.sort(key=lambda x: (x.roi, x.depth_notional), reverse=True)
        else:
            evaluated.sort(key=lambda x: (x.edge, x.depth_notional), reverse=True)

        if allocation == "equal_risk":
            first_budget = mode_budget(conn, mode, evaluated[0].candidate, global_cfg)
            target_each = first_budget / Decimal(len(evaluated)) if evaluated else Decimal(0)
            for item in evaluated:
                allowed = min(target_each, mode_budget(conn, mode, item.candidate, global_cfg))
                place_order(conn, mode, item, allowed)
        elif allocation == "edge_weighted":
            first_budget = mode_budget(conn, mode, evaluated[0].candidate, global_cfg)
            total_edge = sum((item.edge for item in evaluated), Decimal(0))
            for item in evaluated:
                weight = item.edge / total_edge if total_edge > 0 else Decimal(1) / Decimal(len(evaluated))
                target = first_budget * weight
                allowed = min(target, mode_budget(conn, mode, item.candidate, global_cfg))
                place_order(conn, mode, item, allowed)
        else:
            for item in evaluated:
                allowed = mode_budget(conn, mode, item.candidate, global_cfg)
                if allowed <= 0:
                    record_decision(
                        conn, int(mode["portfolio_id"]), item.candidate.signal_id,
                        "skip", Decimal(0), "PORTFOLIO_CAP_REACHED", {"mode": mode["mode_code"]}, item.roi,
                    )
                    continue
                place_order(conn, mode, item, allowed)


def weather_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "station_code": str(row[1]),
        "source": str(row[2]),
        "report_type": str(row[3]),
        "observed_at": row[4],
        "first_seen_at": row[5],
        "received_epoch_ms": int(row[6]),
        "received_epoch_ns": int(row[7]),
        "temperature_f": d(row[8]) if row[8] is not None else None,
        "max_temperature_f": d(row[9]) if row[9] is not None else None,
        "compatibility_status": str(row[10]),
        "compatibility_rule": str(row[11]) if row[11] is not None else None,
    }


def process_weather(conn: psycopg.Connection[Any], weather: dict[str, Any]) -> int:
    station = weather["station_code"]
    meta = STATIONS.get(station)
    if not meta:
        return 0
    strategy = dbn_strategy(conn)
    if not strategy:
        return 0
    confirmed_high = weather["max_temperature_f"] or weather["temperature_f"]
    if confirmed_high is None:
        return 0

    series = meta["series"]
    proven = compatibility_is_proven(conn, weather)
    series_rules = series_rules_before(conn, series, weather["first_seen_at"])
    event_rules = event_rule_candidates(conn, series, weather["first_seen_at"])
    candidates: list[Candidate] = []
    for event in event_rules:
        for market in event["markets"]:
            upper = market_upper_bound(market)
            ticker = str(market.get("ticker") or "")
            if not ticker or upper is None or confirmed_high <= upper:
                continue
            signal_id, auditor_status = insert_signal(
                conn,
                weather=weather,
                event_ticker=event["event_ticker"],
                market=market,
                upper_bound=upper,
                confirmed_high=confirmed_high,
                event_rules=event,
                series_rules=series_rules,
                proven=proven,
            )
            if auditor_status != "approved" or not strategy["paper_trade_enabled"]:
                continue
            if not series_rules or not series_rules.get("fee_type") or series_rules.get("fee_multiplier") is None:
                continue
            candidates.append(Candidate(
                signal_id=signal_id,
                station=station,
                region=meta.get("region", station),
                event_ticker=event["event_ticker"],
                market_ticker=ticker,
                trigger_at=weather["first_seen_at"],
                trigger_epoch_ms=weather["received_epoch_ms"],
                confirmed_high_f=confirmed_high,
                upper_bound_f=upper,
                fee_type=str(series_rules["fee_type"]),
                fee_multiplier=d(series_rules["fee_multiplier"]),
                evidence={
                    "weather_event_id": weather["id"],
                    "event_rules_hash": event["rules_hash"],
                    "series_rules_hash": series_rules["rules_hash"],
                    "compatibility_rule": weather["compatibility_rule"],
                    "confirmed_high_f": confirmed_high,
                    "upper_bound_f": upper,
                    "region": meta.get("region", station),
                },
            ))

    if candidates:
        global_cfg = load_global(conn)
        modes = load_modes(conn)
        execute_candidates(conn, candidates, modes, global_cfg)
    return len(candidates)


def audit_error(conn: psycopg.Connection[Any], code: str, details: dict[str, Any], station: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO audit_findings(session_id,severity,component,finding_code,station_code,details)
        VALUES (%s,'error','paper_engine',%s,%s,%s::jsonb)
        """,
        (SESSION_ID, code, station, json.dumps(details, separators=(",", ":"), default=json_default)),
    )


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"event": "paper_engine_start", "session_id": SESSION_ID, "engine_version": ENGINE_VERSION}))
    with psycopg.connect(DATABASE_URL) as conn:
        ensure_session_and_portfolios(conn)
        while not STOP:
            started = time.monotonic()
            global_cfg = load_global(conn)
            if not bool(global_cfg.get("paper_enabled", True)):
                time.sleep(max(0.05, POLL_MS / 1000))
                continue
            delay_ms = max(1000, int(global_cfg.get("paper_engine_process_delay_ms", DEFAULT_PROCESS_DELAY_MS)))
            state = conn.execute(
                "SELECT last_weather_id FROM paper_engine_state WHERE session_id=%s AND component='paper_engine'",
                (SESSION_ID,),
            ).fetchone()
            last_id = int(state[0]) if state else 0
            cutoff_ms = time.time_ns() // 1_000_000 - delay_ms
            rows = conn.execute(
                """
                SELECT id,station_code,source,report_type,observed_at,first_seen_at,
                       received_epoch_ms,received_epoch_ns,temperature_f,max_temperature_f,
                       compatibility_status,compatibility_rule
                  FROM live_weather_journal
                 WHERE session_id=%s AND id>%s AND received_epoch_ms<=%s
                 ORDER BY id ASC LIMIT 100
                """,
                (SESSION_ID, last_id, cutoff_ms),
            ).fetchall()
            if not rows:
                elapsed = time.monotonic() - started
                time.sleep(max(0.05, POLL_MS / 1000 - elapsed))
                continue

            for raw in rows:
                weather = weather_row(raw)
                try:
                    with conn.transaction():
                        created = process_weather(conn, weather)
                        conn.execute(
                            """
                            UPDATE paper_engine_state
                               SET last_weather_id=%s,updated_at=now()
                             WHERE session_id=%s AND component='paper_engine'
                            """,
                            (weather["id"], SESSION_ID),
                        )
                    if created:
                        print(json.dumps({
                            "event": "paper_weather_processed",
                            "weather_id": weather["id"],
                            "station": weather["station_code"],
                            "approved_candidates": created,
                        }))
                except Exception as exc:
                    conn.rollback()
                    with conn.transaction():
                        audit_error(conn, "PAPER_ENGINE_WEATHER_FAILED", {
                            "weather_id": weather["id"], "error": repr(exc),
                        }, weather["station_code"])
                        conn.execute(
                            """
                            UPDATE paper_engine_state
                               SET last_weather_id=%s,updated_at=now()
                             WHERE session_id=%s AND component='paper_engine'
                            """,
                            (weather["id"], SESSION_ID),
                        )
            conn.commit()
    print(json.dumps({"event": "paper_engine_stop", "session_id": SESSION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
