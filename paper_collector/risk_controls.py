from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg

ONE = Decimal("1")
ZERO = Decimal("0")
MILLION = Decimal("1000000")


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def budget_multiplier(
    conn: psycopg.Connection[Any],
    portfolio_id: int,
    global_cfg: dict[str, Any],
) -> Decimal:
    """Return the live drawdown multiplier for new benchmark entries.

    Missing, incomplete, or stale risk state fails closed when drawdown
    protection is enabled. A monitor failure therefore removes buying power
    instead of silently restoring full risk.
    """
    if not bool(global_cfg.get("drawdown_risk_enabled", True)):
        return ONE
    row = conn.execute(
        "SELECT risk_multiplier,mark_complete,updated_at FROM paper_risk_state WHERE portfolio_id=%s",
        (portfolio_id,),
    ).fetchone()
    if not row:
        return ZERO
    mark_complete = bool(row[1])
    updated_at = row[2]
    max_age = max(5.0, float(global_cfg.get("risk_state_max_age_seconds", 30)))
    if not isinstance(updated_at, datetime):
        return ZERO
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds()
    if age < -5 or age > max_age:
        return ZERO
    if not mark_complete and bool(global_cfg.get("mark_incomplete_blocks_new_entries", True)):
        return ZERO
    return max(ZERO, min(ONE, d(row[0])))


def apply_budget_multiplier(
    conn: psycopg.Connection[Any],
    portfolio_id: int,
    budget: Decimal,
    global_cfg: dict[str, Any],
) -> Decimal:
    return max(ZERO, budget * budget_multiplier(conn, portfolio_id, global_cfg))


def exposure(
    conn: psycopg.Connection[Any],
    portfolio_id: int,
    event_ticker: str,
    region_stations: list[str],
    when: datetime,
    day_timezone: str,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return event risk, region risk, and today's gross deployment.

    Event/region caps are *risk* caps and therefore count only still-open
    positions. The old implementation summed every historical fill forever,
    which made a region permanently unusable after enough past trades.

    The daily cap intentionally remains gross turnover for the local day. Cash
    becoming free after settlement must not allow unlimited same-day recycling.
    """
    event_row = conn.execute(
        """
        SELECT COALESCE(sum(o.total_cost_micros),0)
          FROM paper_orders o
          JOIN paper_signals s ON s.id=o.signal_id
          JOIN paper_positions p
            ON p.portfolio_id=o.portfolio_id
           AND p.market_ticker=o.market_ticker
           AND p.outcome_side=o.outcome_side
           AND p.status='open'
         WHERE o.portfolio_id=%s
           AND o.status IN ('filled','partial')
           AND s.event_ticker=%s
        """,
        (portfolio_id, event_ticker),
    ).fetchone()
    region_row = conn.execute(
        """
        SELECT COALESCE(sum(o.total_cost_micros),0)
          FROM paper_orders o
          JOIN paper_signals s ON s.id=o.signal_id
          JOIN paper_positions p
            ON p.portfolio_id=o.portfolio_id
           AND p.market_ticker=o.market_ticker
           AND p.outcome_side=o.outcome_side
           AND p.status='open'
         WHERE o.portfolio_id=%s
           AND o.status IN ('filled','partial')
           AND s.station_code=ANY(%s)
        """,
        (portfolio_id, region_stations),
    ).fetchone()
    day_row = conn.execute(
        """
        SELECT COALESCE(sum(total_cost_micros),0)
          FROM paper_orders
         WHERE portfolio_id=%s
           AND status IN ('filled','partial')
           AND (decision_at AT TIME ZONE %s)::date=(%s AT TIME ZONE %s)::date
        """,
        (portfolio_id, day_timezone, when, day_timezone),
    ).fetchone()
    return (
        d(event_row[0] if event_row else 0) / MILLION,
        d(region_row[0] if region_row else 0) / MILLION,
        d(day_row[0] if day_row else 0) / MILLION,
    )
