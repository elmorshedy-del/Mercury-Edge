from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

import strategy_engines as se


class StrategyEngineTests(unittest.TestCase):
    def test_binary_asks_are_exact_complements_of_native_bids(self) -> None:
        book = se.FullBook(
            yes_bids={Decimal("0.6700"): Decimal("3")},
            no_bids={Decimal("0.3100"): Decimal("4")},
            connection_id="test",
            last_seq=1,
            snapshot_id=1,
            snapshot_received_ms=1,
        )
        self.assertEqual(se.asks(book, "no")[0], (Decimal("0.3300"), Decimal("3")))
        self.assertEqual(se.asks(book, "yes")[0], (Decimal("0.6900"), Decimal("4")))

    def test_exhaustive_partition_check_fails_closed_without_both_tails(self) -> None:
        interior_only = [
            {"floor_strike": 75, "cap_strike": 76},
            {"floor_strike": 77, "cap_strike": 78},
        ]
        with_tails = [
            {"floor_strike": None, "cap_strike": 74},
            {"floor_strike": 75, "cap_strike": 76},
            {"floor_strike": 77, "cap_strike": None},
        ]
        self.assertFalse(se.event_is_exhaustive_partition(interior_only))
        self.assertTrue(se.event_is_exhaustive_partition(with_tails))

    def test_prv_fires_only_when_coarse_print_crosses_but_precise_value_does_not(self) -> None:
        weather = {
            "id": 1,
            "station_code": "KLAX",
            "source": "IEM_MADIS_OMO",
            "temperature_f": Decimal("76.10"),
            "first_seen_at": datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc),
            "received_epoch_ms": 1_000,
        }
        meta = {"region": "west"}
        event = {
            "event_ticker": "TEST",
            "rules_hash": "r",
            "markets": [{"ticker": "B77", "floor_strike": 77, "cap_strike": 78}],
        }
        bundles = se.construct_prv(weather, meta, event, {"stations": ["KLAX"], "max_no_price": 0.80})
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0].strategy_code, "PRV")
        self.assertEqual(bundles[0].legs[0].side, "no")
        self.assertEqual(bundles[0].evidence["threshold_f"], Decimal("77"))

        weather["temperature_f"] = Decimal("77.10")
        self.assertEqual(se.construct_prv(weather, meta, event, {"stations": ["KLAX"]}), [])

    def test_fee_adjusted_basket_cost_exposes_unprofitable_basket(self) -> None:
        # Three 34c survivor legs cost > $1 before fees, so a guaranteed $1 basket
        # is not an arbitrage and must fail the runtime edge guard.
        total = Decimal(0)
        for _ in range(3):
            cost, fee, _ = se.total_cost([(Decimal("0.34"), Decimal("1"))], Decimal("1"))
            self.assertGreater(fee, Decimal(0))
            total += cost
        self.assertGreater(total, Decimal("1"))

    def test_fee_adjusted_basket_can_preserve_positive_edge(self) -> None:
        total = Decimal(0)
        for _ in range(3):
            cost, _, _ = se.total_cost([(Decimal("0.20"), Decimal("1"))], Decimal("1"))
            total += cost
        self.assertLess(total, Decimal("1"))

    def test_coarse_celsius_conversion_is_explicit(self) -> None:
        precise_f = Decimal("76.10")
        precise_c = se._c_from_f(precise_f)
        coarse_c = precise_c.to_integral_value(rounding=se.ROUND_HALF_UP)
        coarse_f = se._f_from_c(coarse_c)
        self.assertEqual(coarse_c, Decimal("25"))
        self.assertEqual(coarse_f, Decimal("77"))
        self.assertLess(precise_f, coarse_f)


if __name__ == "__main__":
    unittest.main()
