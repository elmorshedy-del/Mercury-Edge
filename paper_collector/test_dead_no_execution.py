from __future__ import annotations

from decimal import Decimal
import unittest

from dead_no_execution import plan_dead_no


class DeadNoExecutionTests(unittest.TestCase):
    def test_profitable_dead_no_plan_uses_exact_guaranteed_terminal_value(self) -> None:
        plan = plan_dead_no(
            [(Decimal("0.60"), Decimal("100"))],
            budget=Decimal("50"),
            fee_multiplier=Decimal("1"),
            max_price=Decimal("0.99"),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertLessEqual(plan.total_cost, Decimal("50"))
        self.assertEqual(plan.guaranteed_payout, plan.filled_qty)
        self.assertEqual(plan.guaranteed_profit, plan.guaranteed_payout - plan.total_cost)
        self.assertGreater(plan.guaranteed_profit, 0)
        self.assertGreater(plan.guaranteed_roi, 0)
        self.assertEqual(plan.avg_fill_price, Decimal("0.60"))

    def test_zero_edge_at_one_dollar_is_never_a_trade(self) -> None:
        self.assertIsNone(plan_dead_no(
            [(Decimal("1.00"), Decimal("10"))],
            budget=Decimal("10"),
            fee_multiplier=Decimal("1"),
            max_price=Decimal("1.00"),
        ))

    def test_fee_and_cash_rounding_can_make_near_one_dollar_fill_unprofitable(self) -> None:
        self.assertIsNone(plan_dead_no(
            [(Decimal("0.9999"), Decimal("0.01"))],
            budget=Decimal("1"),
            fee_multiplier=Decimal("1"),
            max_price=Decimal("1.00"),
        ))

    def test_cheapest_l2_asks_are_consumed_first(self) -> None:
        plan = plan_dead_no(
            [
                (Decimal("0.80"), Decimal("10")),
                (Decimal("0.50"), Decimal("10")),
                (Decimal("0.70"), Decimal("10")),
            ],
            budget=Decimal("12"),
            fee_multiplier=Decimal("1"),
            max_price=Decimal("0.90"),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        prices = [price for price, _ in plan.fills]
        self.assertEqual(prices, sorted(prices))
        self.assertEqual(prices[0], Decimal("0.50"))

    def test_portfolio_price_ceiling_is_only_a_guard_and_is_respected(self) -> None:
        plan = plan_dead_no(
            [
                (Decimal("0.80"), Decimal("1")),
                (Decimal("0.95"), Decimal("100")),
            ],
            budget=Decimal("100"),
            fee_multiplier=Decimal("1"),
            max_price=Decimal("0.90"),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertTrue(all(price <= Decimal("0.90") for price, _ in plan.fills))
        self.assertEqual(plan.filled_qty, Decimal("1"))

    def test_budget_is_fee_aware_not_just_notional_aware(self) -> None:
        plan = plan_dead_no(
            [(Decimal("0.50"), Decimal("100"))],
            budget=Decimal("1.00"),
            fee_multiplier=Decimal("1"),
            max_price=Decimal("0.90"),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertLessEqual(plan.total_cost, Decimal("1.00"))
        self.assertGreater(plan.total_cost, plan.gross_cost)

    def test_same_l2_budget_and_fee_model_produce_identical_plan(self) -> None:
        kwargs = dict(
            asks=[(Decimal("0.55"), Decimal("20")), (Decimal("0.65"), Decimal("20"))],
            budget=Decimal("15"),
            fee_multiplier=Decimal("1"),
            max_price=Decimal("0.90"),
        )
        self.assertEqual(plan_dead_no(**kwargs), plan_dead_no(**kwargs))


if __name__ == "__main__":
    unittest.main()
