from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from market_calendar import (
    climate_date,
    climate_day_bounds,
    confirmed_same_day_high,
    event_matches_observation,
    local_date,
    six_hour_window_within_climate_day,
    standard_utc_offset,
)

UTC = timezone.utc


class ClimateClockTests(unittest.TestCase):
    def test_eastern_dst_0030_belongs_to_previous_climate_date(self) -> None:
        instant = datetime(2026, 8, 19, 4, 30, tzinfo=UTC)  # 00:30 EDT, 23:30 EST
        self.assertEqual(local_date(instant, "America/New_York"), date(2026, 8, 19))
        self.assertEqual(climate_date(instant, "America/New_York"), date(2026, 8, 18))
        self.assertTrue(event_matches_observation("KXHIGHNY-26AUG18", instant, "America/New_York"))
        self.assertFalse(event_matches_observation("KXHIGHNY-26AUG19", instant, "America/New_York"))

    def test_eastern_dst_0100_starts_new_climate_date(self) -> None:
        instant = datetime(2026, 8, 19, 5, 0, tzinfo=UTC)  # 01:00 EDT == 00:00 EST
        self.assertEqual(climate_date(instant, "America/New_York"), date(2026, 8, 19))

    def test_pacific_dst_0030_belongs_to_previous_climate_date(self) -> None:
        instant = datetime(2026, 8, 17, 7, 30, tzinfo=UTC)  # 00:30 PDT, 23:30 PST
        self.assertEqual(local_date(instant, "America/Los_Angeles"), date(2026, 8, 17))
        self.assertEqual(climate_date(instant, "America/Los_Angeles"), date(2026, 8, 16))
        self.assertTrue(event_matches_observation("KXHIGHLAX-26AUG16", instant, "America/Los_Angeles"))

    def test_phoenix_fixed_standard_time_has_midnight_boundary(self) -> None:
        before = datetime(2026, 8, 19, 6, 59, tzinfo=UTC)
        at = datetime(2026, 8, 19, 7, 0, tzinfo=UTC)
        self.assertEqual(climate_date(before, "America/Phoenix"), date(2026, 8, 18))
        self.assertEqual(climate_date(at, "America/Phoenix"), date(2026, 8, 19))

    def test_standard_offsets(self) -> None:
        self.assertEqual(standard_utc_offset("America/New_York", 2026).total_seconds(), -5 * 3600)
        self.assertEqual(standard_utc_offset("America/Chicago", 2026).total_seconds(), -6 * 3600)
        self.assertEqual(standard_utc_offset("America/Denver", 2026).total_seconds(), -7 * 3600)
        self.assertEqual(standard_utc_offset("America/Los_Angeles", 2026).total_seconds(), -8 * 3600)
        self.assertEqual(standard_utc_offset("America/Phoenix", 2026).total_seconds(), -7 * 3600)

    def test_climate_day_bounds_do_not_shift_for_dst(self) -> None:
        ny_start, ny_end = climate_day_bounds(date(2026, 8, 18), "America/New_York")
        self.assertEqual(ny_start, datetime(2026, 8, 18, 5, 0, tzinfo=UTC))
        self.assertEqual(ny_end, datetime(2026, 8, 19, 5, 0, tzinfo=UTC))
        la_start, la_end = climate_day_bounds(date(2026, 8, 16), "America/Los_Angeles")
        self.assertEqual(la_start, datetime(2026, 8, 16, 8, 0, tzinfo=UTC))
        self.assertEqual(la_end, datetime(2026, 8, 17, 8, 0, tzinfo=UTC))

    def test_winter_and_summer_use_same_standard_boundary(self) -> None:
        winter_start, _ = climate_day_bounds(date(2026, 1, 15), "America/New_York")
        summer_start, _ = climate_day_bounds(date(2026, 8, 15), "America/New_York")
        self.assertEqual(winter_start.hour, 5)
        self.assertEqual(summer_start.hour, 5)


class SixHourWindowTests(unittest.TestCase):
    def test_klax_afternoon_six_hour_group_is_wholly_inside_climate_day(self) -> None:
        observed = datetime(2026, 8, 16, 23, 53, tzinfo=UTC)  # 15:53 LST
        self.assertTrue(six_hour_window_within_climate_day(observed, "America/Los_Angeles"))

    def test_klax_early_morning_six_hour_group_crosses_climate_midnight(self) -> None:
        observed = datetime(2026, 8, 16, 11, 53, tzinfo=UTC)  # 03:53 LST
        self.assertFalse(six_hour_window_within_climate_day(observed, "America/Los_Angeles"))


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def execute(self, _query, params):
        self.params = params
        return self

    def fetchall(self):
        return self.rows


class ConfirmedHighCalendarTests(unittest.TestCase):
    def test_confirmed_high_uses_climate_date_and_rejects_cross_midnight_maxT(self) -> None:
        rows = [
            (1, "NOAA_AWC", datetime(2026, 8, 16, 11, 53, tzinfo=UTC), Decimal("72"), Decimal("80")),
            (2, "NOAA_AWC", datetime(2026, 8, 16, 23, 53, tzinfo=UTC), Decimal("74"), Decimal("77")),
        ]
        conn = FakeConnection(rows)
        high = confirmed_same_day_high(
            conn,
            session_id="test",
            station_code="KLAX",
            observed_at=datetime(2026, 8, 16, 23, 53, tzinfo=UTC),
            first_seen_at=datetime(2026, 8, 16, 23, 54, tzinfo=UTC),
            timezone_name="America/Los_Angeles",
        )
        self.assertIsNotNone(high)
        assert high is not None
        self.assertEqual(high.local_trade_date, date(2026, 8, 16))
        self.assertEqual(high.value_f, Decimal("77"))
        self.assertEqual(high.used_awc_six_hour_max_ids, (2,))
        # Query bounds must be 00:00–24:00 PST, i.e. 08Z–08Z during August PDT.
        self.assertEqual(conn.params[3], datetime(2026, 8, 16, 8, 0, tzinfo=UTC))
        self.assertEqual(conn.params[4], datetime(2026, 8, 17, 8, 0, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
