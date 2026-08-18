from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg

ONE = Decimal("1")
ZERO = Decimal("0")


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def budget_multiplier(
    conn: psycopg.Connection[Any],
    portfolio_id: int,
    global_cfg: dict[str, Any],
) -> Decimal:
    """Return the live drawdown multiplier for new benchmark entries.

    Missing, incomplete, or stale risk state fails closed when drawdown
    protection is enabled.  A monitor failure therefore removes buying power
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
