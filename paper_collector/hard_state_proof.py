from __future__ import annotations

"""Settlement-oriented hard-state proof built only from raw ASOS/METAR evidence.

The deterministic Mercury edge should never depend on decoded Celsius being
round-tripped to Fahrenheit. This module queries causally available raw AWC
METAR/SPECI text for one NWS local-standard-time climate day, parses the ASOS
encoding lattice, and returns the strongest *proven lower bound* on that day's
maximum temperature.

Step 3 intentionally accepts only current/raw temperature evidence (main/T group)
and six-hour maximum groups. 24-hour groups/DSM/CLI become first-class evidence
in Step 4.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from asos_evidence import TemperatureEvidence, evidence_by_kind, parse_temperature_evidence
from market_calendar import climate_date, climate_day_bounds, six_hour_window_within_climate_day

H1_CURRENT = "H1_CURRENT"
H2_SIX_HOUR_MAX = "H2_SIX_HOUR_MAX"
_KIND_PRIORITY = {"main_temp_c": 1, "t_group": 2, "six_hour_max": 3}


@dataclass(frozen=True)
class ProofRecord:
    weather_id: int
    source: str
    report_type: str
    observed_at: datetime
    first_seen_at: datetime
    received_epoch_ms: int
    kind: str
    raw_group: str
    encoded_c: Decimal
    possible_canonical_f: tuple[int, ...]
    proven_min_f: int
    proven_max_f: int
    grade: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "weather_id": self.weather_id,
            "source": self.source,
            "report_type": self.report_type,
            "observed_at": self.observed_at,
            "first_seen_at": self.first_seen_at,
            "received_epoch_ms": self.received_epoch_ms,
            "kind": self.kind,
            "raw_group": self.raw_group,
            "encoded_c": self.encoded_c,
            "possible_canonical_f": list(self.possible_canonical_f),
            "proven_min_f": self.proven_min_f,
            "proven_max_f": self.proven_max_f,
            "grade": self.grade,
        }


@dataclass(frozen=True)
class HardStateProof:
    climate_trade_date: date
    proven_daily_high_min_f: int
    trigger_weather_id: int
    trigger_at: datetime
    trigger_epoch_ms: int
    trigger_kind: str
    trigger_raw_group: str
    trigger_grade: str
    source_weather_ids_at_bound: tuple[int, ...]
    supporting_records: tuple[ProofRecord, ...]
    rejected_row_count: int

    @property
    def hard_state_proven(self) -> bool:
        return True

    def is_new_transition(self, current_weather_id: int) -> bool:
        return self.trigger_weather_id == int(current_weather_id)

    def proves_above(self, upper_bound_f: int | Decimal) -> bool:
        return Decimal(self.proven_daily_high_min_f) > Decimal(str(upper_bound_f))

    def as_dict(self) -> dict[str, Any]:
        return {
            "proof_version": "raw-asos-lattice-v1",
            "climate_trade_date": self.climate_trade_date.isoformat(),
            "proven_daily_high_min_f": self.proven_daily_high_min_f,
            "trigger_weather_id": self.trigger_weather_id,
            "trigger_at": self.trigger_at,
            "trigger_epoch_ms": self.trigger_epoch_ms,
            "trigger_kind": self.trigger_kind,
            "trigger_raw_group": self.trigger_raw_group,
            "trigger_grade": self.trigger_grade,
            "source_weather_ids_at_bound": list(self.source_weather_ids_at_bound),
            "supporting_records": [record.as_dict() for record in self.supporting_records],
            "rejected_row_count": self.rejected_row_count,
        }


def _row_evidence_is_coherent(items: list[TemperatureEvidence]) -> bool:
    """Fail closed when precise and coarse groups in one raw METAR contradict."""
    if any(item.integrity_status == "off_lattice" for item in items):
        return False

    mains = evidence_by_kind(items, "main_temp_c")
    t_groups = evidence_by_kind(items, "t_group")
    if mains and t_groups:
        main = mains[0]
        t_group = t_groups[0]
        exact = t_group.exact_canonical_f
        if exact is None or exact not in main.possible_canonical_f:
            return False

    sixes = evidence_by_kind(items, "six_hour_max")
    if sixes and t_groups:
        current_min = t_groups[0].proven_min_f
        six_min = sixes[0].proven_min_f
        if current_min is not None and six_min is not None and six_min < current_min:
            return False
    return True


def _records_for_row(row: tuple[Any, ...], timezone_name: str) -> tuple[list[ProofRecord], bool]:
    weather_id, source, report_type, observed_at, first_seen_at, received_epoch_ms, raw_text = row
    if str(source) != "NOAA_AWC" or not raw_text:
        return [], False

    items = parse_temperature_evidence(str(raw_text))
    if not items or not _row_evidence_is_coherent(items):
        return [], False

    records: list[ProofRecord] = []
    for item in items:
        if not item.hard_state_eligible or item.proven_min_f is None or item.proven_max_f is None:
            continue
        if item.kind == "twenty_four_hour_max":
            # Step 4 maps the 24-hour/DSM/CLI lifecycle explicitly.
            continue
        if item.kind == "six_hour_max":
            if not six_hour_window_within_climate_day(observed_at, timezone_name):
                continue
            grade = H2_SIX_HOUR_MAX
        elif item.kind in ("t_group", "main_temp_c"):
            grade = H1_CURRENT
        else:
            continue
        records.append(ProofRecord(
            weather_id=int(weather_id),
            source=str(source),
            report_type=str(report_type),
            observed_at=observed_at,
            first_seen_at=first_seen_at,
            received_epoch_ms=int(received_epoch_ms),
            kind=item.kind,
            raw_group=item.raw_group,
            encoded_c=item.encoded_c,
            possible_canonical_f=item.possible_canonical_f,
            proven_min_f=int(item.proven_min_f),
            proven_max_f=int(item.proven_max_f),
            grade=grade,
        ))
    return records, True


def proof_for_weather(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    weather: dict[str, Any],
    timezone_name: str,
) -> HardStateProof | None:
    """Return the causally known raw-ASOS lower bound for the current climate day."""
    observed_at = weather.get("observed_at")
    first_seen_at = weather.get("first_seen_at")
    weather_id = weather.get("id")
    if not isinstance(observed_at, datetime) or not isinstance(first_seen_at, datetime) or weather_id is None:
        return None

    day = climate_date(observed_at, timezone_name)
    start_utc, end_utc = climate_day_bounds(day, timezone_name)
    rows = conn.execute(
        """
        SELECT id,source,report_type,observed_at,first_seen_at,received_epoch_ms,raw_text
          FROM live_weather_journal
         WHERE session_id=%s
           AND station_code=%s
           AND source='NOAA_AWC'
           AND raw_text IS NOT NULL
           AND observed_at>=%s
           AND observed_at<%s
           AND (first_seen_at<%s OR (first_seen_at=%s AND id<=%s))
         ORDER BY first_seen_at ASC,id ASC
        """,
        (
            session_id,
            weather.get("station_code"),
            start_utc,
            end_utc,
            first_seen_at,
            first_seen_at,
            int(weather_id),
        ),
    ).fetchall()

    records: list[ProofRecord] = []
    rejected_rows = 0
    for row in rows:
        row_records, coherent = _records_for_row(row, timezone_name)
        if not coherent:
            rejected_rows += 1
            continue
        records.extend(row_records)

    if not records:
        return None

    daily_min = max(record.proven_min_f for record in records)
    at_bound = [record for record in records if record.proven_min_f == daily_min]
    earliest_key = min((record.first_seen_at, record.weather_id) for record in at_bound)
    first_bound = [
        record for record in at_bound
        if (record.first_seen_at, record.weather_id) == earliest_key
    ]
    trigger = max(first_bound, key=lambda record: _KIND_PRIORITY.get(record.kind, 0))
    support = tuple(sorted(
        at_bound,
        key=lambda record: (record.first_seen_at, record.weather_id, -_KIND_PRIORITY.get(record.kind, 0)),
    ))
    source_ids = tuple(dict.fromkeys(record.weather_id for record in support))

    return HardStateProof(
        climate_trade_date=day,
        proven_daily_high_min_f=daily_min,
        trigger_weather_id=trigger.weather_id,
        trigger_at=trigger.first_seen_at,
        trigger_epoch_ms=trigger.received_epoch_ms,
        trigger_kind=trigger.kind,
        trigger_raw_group=trigger.raw_group,
        trigger_grade=trigger.grade,
        source_weather_ids_at_bound=source_ids,
        supporting_records=support,
        rejected_row_count=rejected_rows,
    )
