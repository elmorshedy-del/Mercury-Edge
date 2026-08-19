from __future__ import annotations

"""Time/market semantic guards shared by every paper strategy.

Daily weather markets must never be evaluated with an observation from a
neighboring *climate* day. NWS/ASOS climate days use local standard time (LST)
year-round, so during daylight-saving months the climate-day boundary is one
civil-clock hour after midnight.

Likewise, AWC ``maxT`` is a six-hour maximum, not an all-day running high; it is
only safe for a day's hard-state logic when its full six-hour lookback lies
inside that station's LST climate day.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

CLIMATE_CALENDAR_VERSION = "lst-climate-calendar-v1"

_EVENT_DATE_RE = re.compile(r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})(?:$|-)")
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
SIX_HOURS = timedelta(hours=6)


@dataclass(frozen=True)
class ConfirmedHigh:
    value_f: Decimal
    local_trade_date: date
    evidence_weather_ids: tuple[int, ...]
    used_awc_six_hour_max_ids: tuple[int, ...]


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def event_trade_date(event_ticker: str) -> date | None:
    """Parse the explicit YYMONDD date encoded by Kalshi daily event tickers."""
    match = _EVENT_DATE_RE.search(str(event_ticker).upper())
    if not match:
        return None
    month = _MONTHS.get(match.group("mon"))
    if month is None:
        return None
    try:
        return date(2000 + int(match.group("yy")), month, int(match.group("day")))
    except ValueError:
        return None


def local_date(value: datetime, timezone_name: str) -> date:
    """Civil local date. Kept for non-settlement logic such as short-horizon priors."""
    return aware(value).astimezone(ZoneInfo(timezone_name)).date()


def standard_utc_offset(timezone_name: str, year: int) -> timedelta:
    """Return the zone's standard (non-DST) UTC offset for a given year.

    Mercury's current universe is US stations. Sampling winter and summer and
    taking the smaller UTC offset selects standard time for US DST zones while
    naturally preserving fixed-offset zones such as America/Phoenix.
    """
    zone = ZoneInfo(timezone_name)
    offsets = {
        datetime(year, month, 15, 12, tzinfo=zone).utcoffset()
        for month in (1, 7)
    }
    valid = [offset for offset in offsets if offset is not None]
    if not valid:
        raise ValueError(f"No UTC offset available for {timezone_name} in {year}")
    return min(valid)


def local_standard_time(value: datetime, timezone_name: str) -> datetime:
    """Represent an instant on the station's fixed local-standard-time clock."""
    instant = aware(value)
    civil_year = instant.astimezone(ZoneInfo(timezone_name)).year
    fixed = timezone(standard_utc_offset(timezone_name, civil_year))
    return instant.astimezone(fixed)


def climate_date(value: datetime, timezone_name: str) -> date:
    """NWS/ASOS climate date: date on the local-standard-time clock year-round."""
    return local_standard_time(value, timezone_name).date()


def event_matches_observation(event_ticker: str, observed_at: datetime, timezone_name: str) -> bool:
    target = event_trade_date(event_ticker)
    return target is not None and target == climate_date(observed_at, timezone_name)


def local_day_bounds(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    """Civil-midnight bounds retained for non-settlement callers."""
    zone = ZoneInfo(timezone_name)
    start_local = datetime.combine(day, time.min, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def climate_day_bounds(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    """UTC bounds for 00:00–23:59:59 on the fixed LST climate clock."""
    fixed = timezone(standard_utc_offset(timezone_name, day.year))
    start_standard = datetime.combine(day, time.min, tzinfo=fixed)
    end_standard = start_standard + timedelta(days=1)
    return start_standard.astimezone(timezone.utc), end_standard.astimezone(timezone.utc)


def six_hour_window_within_climate_day(observed_at: datetime, timezone_name: str) -> bool:
    """Whether a six-hour maximum ending at observed_at lies wholly in one climate day."""
    end = aware(observed_at).astimezone(timezone.utc)
    day = climate_date(end, timezone_name)
    start_utc, end_utc = climate_day_bounds(day, timezone_name)
    window_start = end - SIX_HOURS
    return window_start >= start_utc and end < end_utc


def confirmed_same_day_high(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    station_code: str,
    observed_at: datetime,
    first_seen_at: datetime,
    timezone_name: str,
) -> ConfirmedHigh | None:
    """Legacy decoded aggregation retained for research-only callers.

    Deterministic hard-state trading must use ``hard_state_proof`` instead.
    """
    day = climate_date(observed_at, timezone_name)
    start_utc, end_utc = climate_day_bounds(day, timezone_name)
    rows = conn.execute(
        """
        SELECT id,source,report_type,observed_at,temperature_f,max_temperature_f
          FROM live_weather_journal
         WHERE session_id=%s
           AND station_code=%s
           AND observed_at>=%s
           AND observed_at<%s
           AND first_seen_at<=%s
         ORDER BY first_seen_at ASC,id ASC
        """,
        (session_id, station_code, start_utc, end_utc, first_seen_at),
    ).fetchall()
    values: list[Decimal] = []
    evidence_ids: list[int] = []
    six_hour_ids: list[int] = []
    for weather_id, source, report_type, row_observed, temperature_f, max_temperature_f in rows:
        if temperature_f is not None:
            values.append(Decimal(str(temperature_f)))
            evidence_ids.append(int(weather_id))
        if max_temperature_f is None:
            continue
        if str(source) == "NOAA_AWC" and str(report_type).upper() in {"METAR", "SPECI"}:
            if not six_hour_window_within_climate_day(row_observed, timezone_name):
                continue
            six_hour_ids.append(int(weather_id))
        values.append(Decimal(str(max_temperature_f)))
        evidence_ids.append(int(weather_id))
    if not values:
        return None
    return ConfirmedHigh(
        value_f=max(values),
        local_trade_date=day,
        evidence_weather_ids=tuple(dict.fromkeys(evidence_ids)),
        used_awc_six_hour_max_ids=tuple(dict.fromkeys(six_hour_ids)),
    )
