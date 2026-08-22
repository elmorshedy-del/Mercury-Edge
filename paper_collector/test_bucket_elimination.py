from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from bucket_elimination import ELIMINATION_MODEL_VERSION, evaluate_event
from hard_information_domain import HardClimateState

UTC = timezone.utc


def state(
    bound: int = 88,
    *,
    station: str = "KPHL",
    day: date = date(2026, 8, 18),
    evidence_id: str = "evidence:t0311",
) -> HardClimateState:
    return HardClimateState(
        state_id=f"state:{station}:{day}:{bound}:{evidence_id}",
        station_code=station,
        climate_date=day,
        proven_daily_high_min_f=bound,
        first_known_at=datetime(2026, 8, 18, 18, 55, tzinfo=UTC),
        transition_evidence_id=evidence_id,
        supporting_evidence_ids=(evidence_id,),
        state_model_version="hard-state-accumulator-v1",
        calendar_version="lst-climate-calendar-v1",
    )


def event(
    *,
    ticker: str = "KXHIGHPHIL-26AUG18",
    station: str = "KPHL",
    rules_hash: str | None = "rules-abc",
    markets=None,
):
    if markets is None:
        markets = [
            {"ticker": "LE83", "floor_strike": None, "cap_strike": 83, "strike_type": "less"},
            {"ticker": "84-85", "floor_strike": 84, "cap_strike": 85, "strike_type": "between"},
            {"ticker": "86-87", "floor_strike": 86, "cap_strike": 87, "strike_type": "between"},
            {"ticker": "88-89", "floor_strike": 88, "cap_strike": 89, "strike_type": "between"},
            {"ticker": "90PLUS", "floor_strike": 90, "cap_strike": None, "strike_type": "greater"},
        ]
    return {
        "event_ticker": ticker,
        "station_code": station,
        "rules_hash": rules_hash,
        "markets": markets,
    }


class BucketEliminationTests(unittest.TestCase):
    def test_88_eliminates_every_and_only_bucket_capped_below_88(self) -> None:
        result = evaluate_event(event(), state(88))
        self.assertTrue(result.accepted)
        self.assertEqual(result.dead_market_tickers, ("LE83", "84-85", "86-87"))
        by_ticker = {item.market_ticker: item for item in result.eliminations}
        self.assertFalse(by_ticker["88-89"].eliminated)
        self.assertFalse(by_ticker["90PLUS"].eliminated)
        self.assertEqual(by_ticker["86-87"].hard_lower_bound_f, 88)
        self.assertEqual(by_ticker["86-87"].strike_rule, "floor_strike=86;cap_strike=87")
        self.assertEqual(by_ticker["86-87"].elimination_model_version, ELIMINATION_MODEL_VERSION)
        payload = result.to_dict()
        self.assertEqual(payload["transition_evidence_id"], "evidence:t0311")
        self.assertEqual(payload["event_rules_hash"], "rules-abc")

    def test_boundary_equal_to_cap_is_not_dead(self) -> None:
        result = evaluate_event(event(markets=[
            {"ticker": "AT87", "floor_strike": 86, "cap_strike": 87},
            {"ticker": "88PLUS", "floor_strike": 88, "cap_strike": None},
        ]), state(87))
        self.assertTrue(result.accepted)
        self.assertEqual(result.dead_market_tickers, ())
        self.assertEqual(result.eliminations[0].reason, "hard_lower_bound_not_above_market_cap")

    def test_decimal_cap_below_integer_hard_bound_is_dead(self) -> None:
        result = evaluate_event(event(markets=[
            {"ticker": "CAP879", "floor_strike": Decimal("86"), "cap_strike": Decimal("87.9")},
            {"ticker": "88PLUS", "floor_strike": 88, "cap_strike": None},
        ]), state(88))
        self.assertEqual(result.dead_market_tickers, ("CAP879",))

    def test_upper_tail_without_cap_is_valid_but_not_eliminated(self) -> None:
        result = evaluate_event(event(markets=[
            {"ticker": "86-87", "floor_strike": 86, "cap_strike": 87},
            {"ticker": "88PLUS", "floor_strike": 88, "cap_strike": None},
        ]), state(88))
        self.assertTrue(result.accepted)
        upper = result.eliminations[1]
        self.assertFalse(upper.eliminated)
        self.assertEqual(upper.reason, "no_finite_upper_bound")

    def test_source_mechanism_does_not_change_dead_set(self) -> None:
        t_group = evaluate_event(event(), state(88, evidence_id="evidence:T0311"))
        hidden_max = evaluate_event(event(), state(88, evidence_id="evidence:10311"))
        self.assertEqual(t_group.dead_market_tickers, hidden_max.dead_market_tickers)

    def test_aug19_event_cannot_use_aug18_hard_state(self) -> None:
        result = evaluate_event(
            event(ticker="KXHIGHPHIL-26AUG19"),
            state(88, day=date(2026, 8, 18)),
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.fail_closed_reason, "event_climate_date_mismatch")
        self.assertEqual(result.eliminations, ())

    def test_station_mismatch_fails_closed(self) -> None:
        result = evaluate_event(event(station="KNYC"), state(88, station="KPHL"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.fail_closed_reason, "event_station_mismatch")

    def test_unparseable_event_date_fails_closed(self) -> None:
        result = evaluate_event(event(ticker="KXHIGHPHIL-TODAY"), state())
        self.assertFalse(result.accepted)
        self.assertEqual(result.fail_closed_reason, "unparseable_event_date")

    def test_missing_rules_hash_fails_closed(self) -> None:
        result = evaluate_event(event(rules_hash=None), state())
        self.assertFalse(result.accepted)
        self.assertEqual(result.fail_closed_reason, "missing_event_rules_hash")

    def test_malformed_market_metadata_fails_whole_event_closed(self) -> None:
        bad_sets = [
            [{"ticker": "BAD", "floor_strike": None, "cap_strike": None}],
            [{"ticker": "BAD", "floor_strike": "x", "cap_strike": 87}],
            [{"ticker": "BAD", "floor_strike": 90, "cap_strike": 87}],
            [
                {"ticker": "DUP", "floor_strike": None, "cap_strike": 87},
                {"ticker": "DUP", "floor_strike": 88, "cap_strike": None},
            ],
        ]
        for markets in bad_sets:
            with self.subTest(markets=markets):
                result = evaluate_event(event(markets=markets), state())
                self.assertFalse(result.accepted)
                self.assertEqual(result.eliminations, ())

    def test_elimination_is_deterministic(self) -> None:
        first = evaluate_event(event(), state())
        second = evaluate_event(event(), state())
        self.assertEqual(first, second)
        self.assertEqual(first.to_dict(), second.to_dict())


if __name__ == "__main__":
    unittest.main()
