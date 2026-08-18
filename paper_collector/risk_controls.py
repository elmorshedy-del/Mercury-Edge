from __future__ import annotations

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

    A missing/incomplete mark state fails closed when drawdown protection is
    enabled.  This prevents a market-data outage from accidentally restoring
    full buying power.
    """
    if not bool(global_cfg.get("drawdown_risk_enabled", True)):
        return ONE
    row = conn.execute(
        "SELECT risk_multiplier,mark_complete FROM paper_risk_state WHERE portfolio_id=%s",
        (portfolio_id,),
    ).fetchone()
    if not row:
        return ZERO
    mark_complete = bool(row[1])
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
