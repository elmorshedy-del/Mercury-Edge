from __future__ import annotations

from datetime import datetime, timezone
import unittest

from hard_information_domain import EvidenceType
from hard_state_proof import H2_SIX_HOUR_MAX, proof_for_weather

UTC = timezone.utc


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _query, _params):
        return self

    def fetchall(self):
        return self.rows


def row(weather_id, observed_at, first_seen_at, raw, *, raw_source_id=42):
    return (
        weather_id,
        raw_source_id,
        "NOAA_AWC",
        "METAR",
        observed_at,
        first_seen_at,
        int(first_seen_at.timestamp() * 1000),
        raw,
    )


def weather(weather_id, observed_at, first_seen_at, station="KLAX"):
    return {
        "id": weather_id,
        "station_code": station,
        "observed_at": observed_at,
        "first_seen_at": first_seen_at,
    }


class LiveAsosChannelTests(unittest.TestCase):
    def test_one_metar_preserves_lower_current_and_higher_six_hour_max(self):
        observed = datetime(2026, 8, 16, 23, 53, tzinfo=UTC)  # 15:53 LST
        seen = datetime(2026, 8, 16, 23, 54, tzinfo=UTC)
        conn = FakeConnection([row(
            1,
            observed,
            seen,
            "KLAX 162353Z 25015KT 10SM CLR 23/18 A2995 RMK AO2 10250 T02330178",
        )])
        proof = proof_for_weather(
            conn,
            session_id="s",
            weather=weather(1, observed, seen),
            timezone_name="America/Los_Angeles",
        )
        assert proof is not None

        self.assertEqual(proof.proven_daily_high_min_f, 77)
        self.assertEqual(proof.trigger_grade, H2_SIX_HOUR_MAX)
        self.assertEqual({record.kind for record in proof.supporting_records}, {"six_hour_max"})

        all_items = proof.all_canonical_evidence("KLAX")
        by_type = {item.evidence_type: item for item in all_items}
        self.assertIn(EvidenceType.ASOS_MAIN_CURRENT, by_type)
        self.assertIn(EvidenceType.ASOS_T_GROUP_CURRENT, by_type)
        self.assertIn(EvidenceType.ASOS_SIX_HOUR_MAX, by_type)
        self.assertLess(by_type[EvidenceType.ASOS_T_GROUP_CURRENT].proven_min_f, 77)
        self.assertEqual(by_type[EvidenceType.ASOS_SIX_HOUR_MAX].proven_min_f, 77)

        # Only evidence that actually establishes the hard bound authorizes the
        # hard state, while all accepted facts remain available for audit.
        support_types = {item.evidence_type for item in proof.canonical_evidence("KLAX")}
        self.assertEqual(support_types, {EvidenceType.ASOS_SIX_HOUR_MAX})
        self.assertGreater(len(proof.as_dict()["all_records"]), len(proof.as_dict()["supporting_records"]))

    def test_cross_climate_midnight_six_hour_group_is_not_preserved_as_trade_evidence(self):
        observed = datetime(2026, 8, 16, 11, 53, tzinfo=UTC)  # 03:53 LST
        seen = datetime(2026, 8, 16, 11, 54, tzinfo=UTC)
        conn = FakeConnection([row(
            1,
            observed,
            seen,
            "KLAX 161153Z 25005KT 10SM CLR 22/18 A2995 RMK AO2 10250 T02220178",
        )])
        proof = proof_for_weather(
            conn,
            session_id="s",
            weather=weather(1, observed, seen),
            timezone_name="America/Los_Angeles",
        )
        assert proof is not None
        all_types = {item.evidence_type for item in proof.all_canonical_evidence("KLAX")}
        self.assertNotIn(EvidenceType.ASOS_SIX_HOUR_MAX, all_types)
        self.assertIn(EvidenceType.ASOS_T_GROUP_CURRENT, all_types)

    def test_twenty_four_hour_group_remains_isolated_and_non_trading(self):
        observed = datetime(2026, 8, 17, 10, 53, tzinfo=UTC)
        seen = datetime(2026, 8, 17, 10, 54, tzinfo=UTC)
        conn = FakeConnection([row(
            1,
            observed,
            seen,
            "KLAX 171053Z 25006KT 10SM CLR 20/17 A2995 RMK AO2 402500194 T02000167",
        )])
        proof = proof_for_weather(
            conn,
            session_id="s",
            weather=weather(1, observed, seen),
            timezone_name="America/Los_Angeles",
        )
        assert proof is not None
        all_types = {item.evidence_type for item in proof.all_canonical_evidence("KLAX")}
        self.assertNotIn(EvidenceType.ASOS_24H_MAX, all_types)
        self.assertEqual(proof.proven_daily_high_min_f, 68)  # T0200 canonical Fahrenheit

    def test_later_six_hour_corroboration_of_existing_bound_does_not_retrigger(self):
        first_obs = datetime(2026, 8, 16, 20, 53, tzinfo=UTC)
        first_seen = datetime(2026, 8, 16, 20, 54, tzinfo=UTC)
        later_obs = datetime(2026, 8, 16, 23, 53, tzinfo=UTC)
        later_seen = datetime(2026, 8, 16, 23, 54, tzinfo=UTC)
        rows = [
            row(1, first_obs, first_seen, "KLAX 162053Z 25010KT 10SM CLR 25/18 A2995 RMK AO2 T02500178", raw_source_id=41),
            row(2, later_obs, later_seen, "KLAX 162353Z 25015KT 10SM CLR 23/18 A2995 RMK AO2 10250 T02330178", raw_source_id=42),
        ]
        proof = proof_for_weather(
            FakeConnection(rows),
            session_id="s",
            weather=weather(2, later_obs, later_seen),
            timezone_name="America/Los_Angeles",
        )
        assert proof is not None
        self.assertEqual(proof.proven_daily_high_min_f, 77)
        self.assertEqual(proof.trigger_weather_id, 1)
        self.assertFalse(proof.is_new_transition(2))
        self.assertTrue(any(
            record.weather_id == 2 and record.kind == "six_hour_max"
            for record in proof.evidence_records
        ))


if __name__ == "__main__":
    unittest.main()
