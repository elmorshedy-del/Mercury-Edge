from __future__ import annotations

"""Counterfactual paper research that never touches benchmark bankrolls.

Every qualifying signal can be replayed across a latency ladder, a budget ladder,
and (for routers) every route.  These trials are intentionally independent
alternative universes: they answer sensitivity questions quickly without
changing the six $1,000 benchmark sleeves or consuming their cash.
"""

import json
from decimal import Decimal, ROUND_FLOOR
from typing import Any

import psycopg

import strategy_engines as se

QTY_STEP = Decimal("0.01")
ZERO = Decimal("0")


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def _ladders(global_cfg: dict[str, Any]) -> tuple[list[int], list[Decimal]]:
    raw_latencies = global_cfg.get("experiment_latency_ladder_ms") or global_cfg.get("latency_ladder_ms") or [0, 50, 100, 250, 500, 1000]
    raw_budgets = global_cfg.get("experiment_budget_ladder") or [10, 25, 50, 100, 200]
    latencies = sorted({max(0, int(x)) for x in raw_latencies})
    budgets = sorted({d(x) for x in raw_budgets if d(x) > 0})
    return latencies, budgets


def _equal_quantity_fills(
    book_levels: list[tuple[se.Leg, se.FullBook, list[tuple[Decimal, Decimal]]]],
    budget: Decimal,
    fee_multiplier: Decimal,
) -> list[tuple[se.Leg, se.FullBook, list[tuple[Decimal, Decimal]], Decimal, Decimal, list[dict[str, str]]]]:
    if not book_levels or budget <= 0:
        return []
    max_units = min(
        int((sum((qty for _, qty in levels), ZERO) / QTY_STEP).to_integral_value(rounding=ROUND_FLOOR))
        for _, _, levels in book_levels
    )
    lo, hi, best = 0, max_units, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        qty = QTY_STEP * mid
        total = ZERO
        valid = True
        for _, _, levels in book_levels:
            remaining = qty
            fills: list[tuple[Decimal, Decimal]] = []
            for price, available in levels:
                take = min(available, remaining)
                if take > 0:
                    fills.append((price, take))
                    remaining -= take
                if remaining <= 0:
                    break
            if remaining > 0:
                valid = False
                break
            cost, _, _ = se.total_cost(fills, fee_multiplier)
            total += cost
        if valid and total <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best <= 0:
        return []

    target = QTY_STEP * best
    executions = []
    for leg, book, levels in book_levels:
        remaining = target
        fills: list[tuple[Decimal, Decimal]] = []
        for price, available in levels:
            take = min(available, remaining)
            if take > 0:
                fills.append((price, take))
                remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            return []
        cost, fee, breakdown = se.total_cost(fills, fee_multiplier)
        executions.append((leg, book, fills, cost, fee, breakdown))
    return executions


def _split_budget_fills(
    book_levels: list[tuple[se.Leg, se.FullBook, list[tuple[Decimal, Decimal]]]],
    budget: Decimal,
    fee_multiplier: Decimal,
) -> list[tuple[se.Leg, se.FullBook, list[tuple[Decimal, Decimal]], Decimal, Decimal, list[dict[str, str]]]]:
    if not book_levels or budget <= 0:
        return []
    per_leg = budget / Decimal(len(book_levels))
    executions = []
    for leg, book, levels in book_levels:
        fills, cost, fee, breakdown = se.fills_for_budget(levels, per_leg, fee_multiplier)
        if not fills:
            return []
        executions.append((leg, book, fills, cost, fee, breakdown))
    return executions


def _expected_payout(
    bundle: se.Bundle,
    variant: str,
    executions: list[tuple[se.Leg, se.FullBook, list[tuple[Decimal, Decimal]], Decimal, Decimal, list[dict[str, str]]]],
) -> Decimal | None:
    quantities = [sum((q for _, q in fills), ZERO) for _, _, fills, _, _, _ in executions]
    if not quantities:
        return None
    if variant == "dsn" or variant.startswith("dbn"):
        return sum(quantities, ZERO)
    if variant == "sbk" or bundle.equal_leg_quantity:
        if max(quantities) - min(quantities) <= QTY_STEP / 100:
            return quantities[0]
    if len(executions) == 1:
        leg = executions[0][0]
        if leg.model_probability is not None:
            return leg.model_probability * quantities[0]
        if bundle.signal_class.startswith("hard_state") and leg.side == "no":
            return quantities[0]
    return None


def _insert_trial(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    signal_id: int,
    strategy_code: str,
    variant: str,
    latency_ms: int,
    budget: Decimal,
    status: str,
    gross_cost: Decimal = ZERO,
    fee: Decimal = ZERO,
    expected_payout: Decimal | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    expected_edge = expected_payout - gross_cost - fee if expected_payout is not None else None
    conn.execute(
        """
        INSERT INTO paper_shadow_trials(
          session_id,signal_id,strategy_code,variant,latency_ms,budget,status,
          gross_cost,estimated_fee,expected_payout,expected_edge,details
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        ON CONFLICT (session_id,signal_id,variant,latency_ms,budget) DO NOTHING
        """,
        (
            session_id,
            signal_id,
            strategy_code,
            variant,
            latency_ms,
            budget,
            status,
            gross_cost,
            fee,
            expected_payout,
            expected_edge,
            json.dumps(details or {}, separators=(",", ":"), default=se.json_default),
        ),
    )


def record_bundle_trials(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    bundle: se.Bundle,
    signal_id: int,
    global_cfg: dict[str, Any],
    strategy_cfg: dict[str, Any],
    fee_multiplier: Decimal,
) -> int:
    if not bool(global_cfg.get("paper_experiment_lab_enabled", True)):
        return 0

    latencies, budgets = _ladders(global_cfg)
    inter_leg = max(0, int(strategy_cfg.get("config", {}).get("multi_leg_inter_order_ms", 5)))
    routes = bundle.route_options if bundle.route_options else {bundle.strategy_code.lower(): bundle.legs}
    written = 0

    for variant, legs in routes.items():
        if not legs:
            continue
        for latency in latencies:
            book_levels: list[tuple[se.Leg, se.FullBook, list[tuple[Decimal, Decimal]]]] = []
            for index, leg in enumerate(legs):
                arrival_ms = leg.trigger_epoch_ms + latency + index * inter_leg
                book = se.reconstruct_full_book(conn, session_id, leg.market_ticker, arrival_ms)
                levels = se.asks(book, leg.side, leg.max_price) if book else []
                if not book or not levels:
                    book_levels = []
                    break
                book_levels.append((leg, book, levels))

            for budget in budgets:
                if not book_levels:
                    _insert_trial(
                        conn,
                        session_id=session_id,
                        signal_id=signal_id,
                        strategy_code=bundle.strategy_code,
                        variant=variant,
                        latency_ms=latency,
                        budget=budget,
                        status="blocked",
                        details={"reason": "NO_VALID_L2_AT_COUNTERFACTUAL_ARRIVAL"},
                    )
                    written += 1
                    continue

                equal = bundle.equal_leg_quantity or variant == "sbk"
                executions = (
                    _equal_quantity_fills(book_levels, budget, fee_multiplier)
                    if equal
                    else _split_budget_fills(book_levels, budget, fee_multiplier)
                )
                if len(executions) != len(book_levels):
                    _insert_trial(
                        conn,
                        session_id=session_id,
                        signal_id=signal_id,
                        strategy_code=bundle.strategy_code,
                        variant=variant,
                        latency_ms=latency,
                        budget=budget,
                        status="unfilled",
                        details={"reason": "COUNTERFACTUAL_FOK_NOT_SATISFIED", "legs": len(book_levels)},
                    )
                    written += 1
                    continue

                gross = ZERO
                fee = ZERO
                leg_details: list[dict[str, Any]] = []
                for leg, book, fills, cost, leg_fee, _ in executions:
                    qty = sum((q for _, q in fills), ZERO)
                    leg_gross = sum((p * q for p, q in fills), ZERO)
                    gross += leg_gross
                    fee += leg_fee
                    leg_details.append({
                        "market_ticker": leg.market_ticker,
                        "side": leg.side,
                        "qty": format(qty, "f"),
                        "gross_cost": format(leg_gross, "f"),
                        "fee": format(leg_fee, "f"),
                        "avg_fill_price": format(leg_gross / qty, "f") if qty > 0 else None,
                        "fills": [[format(p, "f"), format(q, "f")] for p, q in fills],
                        "book_seq": book.last_seq,
                        "connection_id": book.connection_id,
                    })

                expected = _expected_payout(bundle, variant, executions)
                _insert_trial(
                    conn,
                    session_id=session_id,
                    signal_id=signal_id,
                    strategy_code=bundle.strategy_code,
                    variant=variant,
                    latency_ms=latency,
                    budget=budget,
                    status="filled",
                    gross_cost=gross,
                    fee=fee,
                    expected_payout=expected,
                    details={
                        "signal_class": bundle.signal_class,
                        "auditor_status": bundle.auditor_status,
                        "event_ticker": bundle.event_ticker,
                        "weather_id": bundle.weather_id,
                        "inter_leg_ms": inter_leg,
                        "legs": leg_details,
                    },
                )
                written += 1
    return written
