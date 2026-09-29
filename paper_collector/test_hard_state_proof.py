from datetime import datetime, timezone
import json
import unittest

from hard_information_domain import EvidenceTrust, EvidenceType, IntegrityStatus
from hard_state_proof import H1_CURRENT, H2_SIX_HOUR_MAX, proof_for_weather
from market_calendar import CLIMATE_CALENDAR_VERSION

UTC = timezone.utc


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def execute(self, _query, params):
        self.params = params
        return self

    def fetchall(self):
        return self.rows


def row(weather_id, observed_at, first_seen_at, raw, source="NOAA_AWC", report_type="METAR", raw_source_id=None):
    return (
        weather_id,
        raw_source_id,
        source,
        report_type,
        observed_at,
        first_seen_at,
        int(first_seen_at.timestamp() * 1000),
        raw,
    )


def weather(weather_id, observed_at, first_seen_at, station="KPHL"):
    return {
        "id": weather_id,
        "station_code": station,
        "observed_at": observed_at,
        "first_seen_at": first_seen_at,
    }


class HardStateProofTests(unittest.TestCase):
    def test_t0311_proves_daily_high_at_least_88(self) -> None:
        obs = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        conn = FakeConnection([row(1, obs, seen, "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2 T03110200")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(1, obs, seen), timezone_name="America/New_York")
        self.assertIsNotNone(proof)
        assert proof is not None
        self.assertEqual(proof.proven_daily_high_min_f, 88)
        self.assertTrue(proof.proves_above(87))
        self.assertEqual(proof.trigger_kind, "t_group")
        self.assertEqual(proof.trigger_grade, H1_CURRENT)
        self.assertTrue(proof.is_new_transition(1))

    def test_main_31c_does_not_prove_above_87(self) -> None:
        obs = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        conn = FakeConnection([row(1, obs, seen, "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(1, obs, seen), timezone_name="America/New_York")
        self.assertIsNotNone(proof)
        assert proof is not None
        self.assertEqual(proof.proven_daily_high_min_f, 87)
        self.assertFalse(proof.proves_above(87))

    def test_main_32c_proves_at_least_89(self) -> None:
        obs = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        conn = FakeConnection([row(1, obs, seen, "KPHL 181854Z 22008KT 10SM CLR 32/20 A2992 RMK AO2")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(1, obs, seen), timezone_name="America/New_York")
        assert proof is not None
        self.assertEqual(proof.proven_daily_high_min_f, 89)
        self.assertTrue(proof.proves_above(87))

    def test_off_lattice_t_group_rejects_entire_row(self) -> None:
        obs = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        conn = FakeConnection([row(1, obs, seen, "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2 T03100200")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(1, obs, seen), timezone_name="America/New_York")
        self.assertIsNone(proof)

    def test_main_t_group_conflict_rejects_entire_row(self) -> None:
        obs = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        conn = FakeConnection([row(1, obs, seen, "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2 T03170200")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(1, obs, seen), timezone_name="America/New_York")
        self.assertIsNone(proof)

    def test_six_hour_max_can_raise_bound_while_current_temperature_is_lower(self) -> None:
        obs = datetime(2026, 8, 16, 23, 53, tzinfo=UTC)  # 15:53 LST KLAX
        seen = datetime(2026, 8, 16, 23, 54, tzinfo=UTC)
        conn = FakeConnection([row(1, obs, seen, "KLAX 162353Z 25015KT 10SM CLR 23/18 A2995 RMK AO2 10250 T02330178")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(1, obs, seen, "KLAX"), timezone_name="America/Los_Angeles")
        assert proof is not None
        self.assertEqual(proof.proven_daily_high_min_f, 77)
        self.assertEqual(proof.trigger_kind, "six_hour_max")
        self.assertEqual(proof.trigger_grade, H2_SIX_HOUR_MAX)

    def test_cross_midnight_six_hour_max_is_not_used(self) -> None:
        obs = datetime(2026, 8, 16, 11, 53, tzinfo=UTC)  # 03:53 LST KLAX
        seen = datetime(2026, 8, 16, 11, 54, tzinfo=UTC)
        conn = FakeConnection([row(1, obs, seen, "KLAX 161153Z 25005KT 10SM CLR 22/18 A2995 RMK AO2 10250 T02220178")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(1, obs, seen, "KLAX"), timezone_name="America/Los_Angeles")
        assert proof is not None
        self.assertEqual(proof.proven_daily_high_min_f, 72)
        self.assertEqual(proof.trigger_kind, "t_group")

    def test_repeated_same_bound_is_not_new_transition(self) -> None:
        obs1 = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen1 = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        obs2 = datetime(2026, 8, 18, 19, 54, tzinfo=UTC)
        seen2 = datetime(2026, 8, 18, 19, 55, tzinfo=UTC)
        rows = [
            row(1, obs1, seen1, "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2 T03110200"),
            row(2, obs2, seen2, "KPHL 181954Z 22008KT 10SM CLR 31/20 A2992 RMK AO2 T03110200"),
        ]
        proof = proof_for_weather(FakeConnection(rows), session_id="s", weather=weather(2, obs2, seen2), timezone_name="America/New_York")
        assert proof is not None
        self.assertEqual(proof.trigger_weather_id, 1)
        self.assertFalse(proof.is_new_transition(2))

    def test_higher_later_print_creates_new_transition(self) -> None:
        obs1 = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen1 = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        obs2 = datetime(2026, 8, 18, 19, 54, tzinfo=UTC)
        seen2 = datetime(2026, 8, 18, 19, 55, tzinfo=UTC)
        rows = [
            row(1, obs1, seen1, "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2 T03110200"),
            row(2, obs2, seen2, "KPHL 181954Z 22008KT 10SM CLR 32/20 A2992 RMK AO2 T03170200"),
        ]
        proof = proof_for_weather(FakeConnection(rows), session_id="s", weather=weather(2, obs2, seen2), timezone_name="America/New_York")
        assert proof is not None
        self.assertEqual(proof.proven_daily_high_min_f, 89)
        self.assertEqual(proof.trigger_weather_id, 2)
        self.assertTrue(proof.is_new_transition(2))

    def test_query_uses_lst_climate_bounds(self) -> None:
        obs = datetime(2026, 8, 19, 4, 30, tzinfo=UTC)  # climate date Aug 18 in ET
        seen = datetime(2026, 8, 19, 4, 31, tzinfo=UTC)
        conn = FakeConnection([])
        proof_for_weather(conn, session_id="s", weather=weather(7, obs, seen), timezone_name="America/New_York")
        self.assertEqual(conn.params[2], datetime(2026, 8, 18, 5, 0, tzinfo=UTC))
        self.assertEqual(conn.params[3], datetime(2026, 8, 19, 5, 0, tzinfo=UTC))

    def test_proof_adapts_to_canonical_evidence_and_hard_state(self) -> None:
        obs = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        conn = FakeConnection([row(7, obs, seen, "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2 T03110200")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(7, obs, seen), timezone_name="America/New_York")
        assert proof is not None

        evidence = proof.canonical_evidence("KPHL")
        self.assertGreaterEqual(len(evidence), 1)
        t_item = next(item for item in evidence if item.evidence_type is EvidenceType.ASOS_T_GROUP_CURRENT)
        self.assertEqual(t_item.proven_min_f, 88)
        self.assertEqual(t_item.integrity_status, IntegrityStatus.CANONICAL)
        self.assertEqual(t_item.trust, EvidenceTrust.BENCHMARK_ELIGIBLE)
        self.assertTrue(t_item.benchmark_eligible)
        self.assertEqual(t_item.source_record_ids, ("live_weather_journal:7",))
        self.assertIsNone(t_item.clocks.first_fetchable_at)  # never fabricate a clock we do not store
        self.assertFalse(proof.immutable_provenance_complete)

        state = proof.to_hard_state("KPHL")
        self.assertEqual(state.station_code, "KPHL")
        self.assertEqual(state.climate_date.isoformat(), "2026-08-18")
        self.assertEqual(state.proven_daily_high_min_f, 88)
        self.assertTrue(state.proves_above(87))
        self.assertEqual(state.calendar_version, CLIMATE_CALENDAR_VERSION)
        self.assertIn(state.transition_evidence_id, state.supporting_evidence_ids)
        json.dumps(state.to_dict(), sort_keys=True)

    def test_new_capture_proof_links_to_immutable_raw_source(self) -> None:
        obs = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        conn = FakeConnection([row(
            7, obs, seen,
            "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2 T03110200",
            raw_source_id=42,
        )])
        proof = proof_for_weather(conn, session_id="s", weather=weather(7, obs, seen), timezone_name="America/New_York")
        assert proof is not None
        self.assertTrue(proof.immutable_provenance_complete)
        self.assertEqual(proof.raw_source_ids_at_bound, (42,))
        evidence = proof.canonical_evidence("KPHL")
        self.assertTrue(evidence)
        self.assertTrue(all(item.source_record_ids == ("raw_source_journal:42",) for item in evidence))
        self.assertTrue(all(item.metadata["immutable_raw_provenance"] for item in evidence))

    def test_lossy_main_field_is_canonical_ambiguous_evidence_not_a_guessed_rounding(self) -> None:
        obs = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
        seen = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        conn = FakeConnection([row(9, obs, seen, "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(9, obs, seen), timezone_name="America/New_York")
        assert proof is not None
        evidence = proof.canonical_evidence("KPHL")
        self.assertEqual(len(evidence), 1)
        item = evidence[0]
        self.assertEqual(item.evidence_type, EvidenceType.ASOS_MAIN_CURRENT)
        self.assertEqual(item.integrity_status, IntegrityStatus.AMBIGUOUS)
        self.assertEqual(item.possible_canonical_f, (87, 88))
        self.assertEqual(item.proven_min_f, 87)
        self.assertTrue(item.benchmark_eligible)

    def test_six_hour_hidden_max_adapts_as_distinct_evidence_type(self) -> None:
        obs = datetime(2026, 8, 16, 23, 53, tzinfo=UTC)
        seen = datetime(2026, 8, 16, 23, 54, tzinfo=UTC)
        conn = FakeConnection([row(11, obs, seen, "KLAX 162353Z 25015KT 10SM CLR 23/18 A2995 RMK AO2 10250 T02330178")])
        proof = proof_for_weather(conn, session_id="s", weather=weather(11, obs, seen, "KLAX"), timezone_name="America/Los_Angeles")
        assert proof is not None
        evidence = proof.canonical_evidence("KLAX")
        six = next(item for item in evidence if item.evidence_type is EvidenceType.ASOS_SIX_HOUR_MAX)
        self.assertEqual(six.proven_min_f, 77)
        self.assertEqual(six.raw_identifier, "10250")
        self.assertTrue(six.benchmark_eligible)


if __name__ == "__main__":
    unittest.main()
