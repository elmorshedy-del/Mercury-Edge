from datetime import datetime, timezone
import unittest

from hard_state_proof import H1_CURRENT, H2_SIX_HOUR_MAX, proof_for_weather

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


def row(weather_id, observed_at, first_seen_at, raw, source="NOAA_AWC", report_type="METAR"):
    return (weather_id, source, report_type, observed_at, first_seen_at, int(first_seen_at.timestamp() * 1000), raw)


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


if __name__ == "__main__":
    unittest.main()
