from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://example.invalid/mercury_test")
os.environ.setdefault("PAPER_SESSION_ID", "test-hard-state-accumulator-session")

import hard_state_proof as hp
import paper_engine_hardened as dbn

UTC = timezone.utc


class FakeResult:
    def fetchone(self):
        return None


class FakeConnection:
    def execute(self, _query, _params=None):
        return FakeResult()


def record(weather_id: int, bound: int, seen_minute: int, *, kind: str = "t_group") -> hp.ProofRecord:
    seen = datetime(2026, 8, 18, 18, seen_minute, tzinfo=UTC)
    observed = datetime(2026, 8, 18, 18, max(0, seen_minute - 1), tzinfo=UTC)
    encoded = {
        74: Decimal("23.3"),
        77: Decimal("25.0"),
        86: Decimal("30.0"),
        87: Decimal("30.6"),
        88: Decimal("31.1"),
    }.get(bound, Decimal(str(bound)))
    return hp.ProofRecord(
        weather_id=weather_id,
        raw_source_id=None,
        source="NOAA_AWC",
        report_type="METAR",
        observed_at=observed,
        first_seen_at=seen,
        received_epoch_ms=int(seen.timestamp() * 1000),
        kind=kind,
        raw_group=("10250" if kind == "six_hour_max" else f"T{str(encoded).replace('.', '').zfill(4)}"),
        encoded_c=encoded,
        possible_canonical_f=(bound,),
        proven_min_f=bound,
        proven_max_f=bound,
        grade=hp.H2_SIX_HOUR_MAX if kind == "six_hour_max" else hp.H1_CURRENT,
    )


def proof(all_records: tuple[hp.ProofRecord, ...], *, legacy_trigger_weather_id: int) -> hp.HardStateProof:
    strongest = max(all_records, key=lambda item: item.proven_min_f)
    bound = strongest.proven_min_f
    at_bound = tuple(item for item in all_records if item.proven_min_f == bound)
    return hp.HardStateProof(
        climate_trade_date=date(2026, 8, 18),
        proven_daily_high_min_f=bound,
        trigger_weather_id=legacy_trigger_weather_id,
        trigger_at=strongest.first_seen_at,
        trigger_epoch_ms=strongest.received_epoch_ms,
        trigger_kind=strongest.kind,
        trigger_raw_group=strongest.raw_group,
        trigger_grade=strongest.grade,
        source_weather_ids_at_bound=tuple(item.weather_id for item in at_bound),
        supporting_records=at_bound,
        rejected_row_count=0,
        all_records=all_records,
    )


def weather(weather_id: int, seen_minute: int) -> dict:
    seen = datetime(2026, 8, 18, 18, seen_minute, tzinfo=UTC)
    observed = datetime(2026, 8, 18, 18, max(0, seen_minute - 1), tzinfo=UTC)
    return {
        "id": weather_id,
        "station_code": "KPHL",
        "source": "NOAA_AWC",
        "report_type": "METAR",
        "observed_at": observed,
        "first_seen_at": seen,
        "received_epoch_ms": int(seen.timestamp() * 1000),
        "received_epoch_ns": int(seen.timestamp() * 1_000_000_000),
        "temperature_f": Decimal("75"),
        "max_temperature_f": Decimal("75"),
        "compatibility_status": "unverified",
        "compatibility_rule": None,
    }


def event() -> dict:
    return {
        "event_ticker": "KXHIGHPHIL-26AUG18",
        "rules_hash": "event-rules",
        "markets": [
            {"ticker": "PHL-LOW", "floor_strike": None, "cap_strike": 87},
            {"ticker": "PHL-UP", "floor_strike": 88, "cap_strike": None},
        ],
    }


class CanonicalAccumulatorDbnIntegrationTests(unittest.TestCase):
    def test_accumulator_transition_not_legacy_trigger_authorizes_dbn(self) -> None:
        r1 = record(1, 86, 10)
        r2 = record(2, 88, 20)
        p = proof((r1, r2), legacy_trigger_weather_id=1)  # intentionally stale legacy trigger
        captured = []
        with patch.object(dbn.hard_state_proof, "proof_for_weather", return_value=p), \
             patch.object(dbn.base, "dbn_strategy", return_value={"paper_trade_enabled": True}), \
             patch.object(dbn.base, "series_rules_before", return_value={"rules_hash": "series", "fee_type": "quadratic", "fee_multiplier": Decimal("1")}), \
             patch.object(dbn.base, "event_rule_candidates", return_value=[event()]), \
             patch.object(dbn.base, "insert_signal", return_value=(10, "approved")) as insert_signal, \
             patch.object(dbn.base, "load_global", return_value={}), \
             patch.object(dbn.base, "load_modes", return_value=[]), \
             patch.object(dbn.base, "execute_candidates", side_effect=lambda _c, candidates, _m, _g: captured.extend(candidates)):
            count = dbn.process_weather(FakeConnection(), weather(2, 20))
        self.assertEqual(count, 1)
        self.assertEqual(insert_signal.call_args.kwargs["confirmed_high"], Decimal("88"))
        self.assertEqual(captured[0].evidence["hard_climate_state"]["proven_daily_high_min_f"], 88)
        self.assertEqual(captured[0].evidence["hard_state_timeline"]["current_bound_f"], 88)

    def test_later_lower_row_cannot_retrigger_even_if_legacy_trigger_claims_it_did(self) -> None:
        r1 = record(1, 86, 10)
        r2 = record(2, 88, 20)
        r3 = record(3, 87, 30)
        p = proof((r1, r2, r3), legacy_trigger_weather_id=3)  # intentionally wrong
        with patch.object(dbn.hard_state_proof, "proof_for_weather", return_value=p), \
             patch.object(dbn.base, "dbn_strategy", return_value={"paper_trade_enabled": True}), \
             patch.object(dbn.base, "insert_signal") as insert_signal:
            count = dbn.process_weather(FakeConnection(), weather(3, 30))
        self.assertEqual(count, 0)
        insert_signal.assert_not_called()

    def test_same_receipt_current_and_six_hour_hidden_max_create_single_77_state(self) -> None:
        current = record(7, 74, 20)
        hidden = record(7, 77, 20, kind="six_hour_max")
        p = proof((current, hidden), legacy_trigger_weather_id=7)
        timeline = dbn._timeline_from_proof(p, "KPHL")
        assert timeline is not None
        self.assertEqual(len(timeline.states), 1)
        self.assertEqual(timeline.current_bound_f, 77)
        self.assertEqual(timeline.states[0].first_known_at, hidden.first_seen_at)


if __name__ == "__main__":
    unittest.main()
