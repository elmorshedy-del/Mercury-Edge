from __future__ import annotations

"""Pure execution mathematics for deterministic dead-NO paper trades.

This module has no weather, strike, strategy, database, or network logic. It
assumes upstream code has already supplied an actually eliminated market and an
executable NO ask ladder reconstructed from causal Kalshi L2.

For a dead NO, each filled contract has a terminal settlement value of $1 if the
hard-state interpretation is correct. Therefore the execution question is
fully deterministic:

    guaranteed_payout = filled_quantity
    guaranteed_profit = guaranteed_payout - gross_cost - fees
    guaranteed_roi = guaranteed_profit / (gross_cost + fees)

A plan is executable only when the exact fee-adjusted guaranteed profit is
strictly positive. This is intentionally different from ranking on a raw ask
price or a midpoint.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Iterable

ONE = Decimal("1")
CENT = Decimal("0.01")
TEN_THOUSANDTH = Decimal("0.0001")
QTY_STEP = Decimal("0.01")
EXECUTION_MATH_VERSION = "dead-no-execution-math-v1"


@dataclass(frozen=True)
class DeadNoPlan:
    fills: tuple[tuple[Decimal, Decimal], ...]
    gross_cost: Decimal
    fee: Decimal
    total_cost: Decimal
    filled_qty: Decimal
    guaranteed_payout: Decimal
    guaranteed_profit: Decimal
    guaranteed_roi: Decimal
    avg_fill_price: Decimal
    worst_fill_price: Decimal
    fee_breakdown: tuple[dict[str, str], ...]
    execution_math_version: str = EXECUTION_MATH_VERSION

    @property
    def profitable(self) -> bool:
        return self.guaranteed_profit > 0 and self.total_cost > 0


def ceil_increment(value: Decimal, increment: Decimal) -> Decimal:
    if value <= 0:
        return Decimal(0)
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def fee_for_fills(
    fills: Iterable[tuple[Decimal, Decimal]],
    multiplier: Decimal,
) -> tuple[Decimal, tuple[dict[str, str], ...]]:
    """Mirror the existing Kalshi paper fee/cash-rounding model exactly."""
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
    return total, tuple(details)


def total_cost(
    fills: Iterable[tuple[Decimal, Decimal]],
    multiplier: Decimal,
) -> tuple[Decimal, Decimal, tuple[dict[str, str], ...]]:
    materialized = tuple(fills)
    gross = sum((price * qty for price, qty in materialized), Decimal(0))
    fee, breakdown = fee_for_fills(materialized, multiplier)
    return gross + fee, fee, breakdown


def fills_for_budget(
    asks: Iterable[tuple[Decimal, Decimal]],
    budget: Decimal,
    multiplier: Decimal,
) -> tuple[tuple[tuple[Decimal, Decimal], ...], Decimal, Decimal, tuple[dict[str, str], ...]]:
    """Consume cheapest executable asks first, with exact fee-aware budgeting."""
    if budget <= 0:
        return (), Decimal(0), Decimal(0), ()

    normalized = sorted(
        (
            (Decimal(price), Decimal(qty))
            for price, qty in asks
            if Decimal(qty) > 0 and Decimal(price) >= 0 and Decimal(price) <= ONE
        ),
        key=lambda item: item[0],
    )
    fills: list[tuple[Decimal, Decimal]] = []
    for price, available_qty in normalized:
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
    return tuple(fills), cost, fee, breakdown


def plan_dead_no(
    asks: Iterable[tuple[Decimal, Decimal]],
    *,
    budget: Decimal,
    fee_multiplier: Decimal,
    max_price: Decimal = ONE,
) -> DeadNoPlan | None:
    """Return the largest budget-feasible positive-guarantee dead-NO plan.

    ``asks`` must be an executable NO ask ladder, not midpoint/candle data.
    ``max_price`` is an optional portfolio risk ceiling; profitability is still
    checked from the exact resulting fills after fees and cannot be inferred
    from the ceiling alone.
    """
    if budget <= 0 or max_price < 0:
        return None

    eligible = tuple(
        (Decimal(price), Decimal(qty))
        for price, qty in asks
        if Decimal(qty) > 0 and Decimal(price) >= 0 and Decimal(price) <= max_price
    )
    if not eligible:
        return None

    fills, cost, fee, breakdown = fills_for_budget(eligible, budget, fee_multiplier)
    if not fills or cost <= 0:
        return None

    qty = sum((fill_qty for _, fill_qty in fills), Decimal(0))
    if qty <= 0:
        return None
    gross = sum((price * fill_qty for price, fill_qty in fills), Decimal(0))
    payout = qty
    profit = payout - cost
    if profit <= 0:
        return None
    roi = profit / cost
    avg = gross / qty
    worst = max(price for price, _ in fills)
    return DeadNoPlan(
        fills=fills,
        gross_cost=gross,
        fee=fee,
        total_cost=cost,
        filled_qty=qty,
        guaranteed_payout=payout,
        guaranteed_profit=profit,
        guaranteed_roi=roi,
        avg_fill_price=avg,
        worst_fill_price=worst,
        fee_breakdown=breakdown,
    )
