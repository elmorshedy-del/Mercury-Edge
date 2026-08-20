from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from hard_information_domain import RawSourceRecord, SourceClocks
from hard_state_accumulator import ApplicationStatus, accumulate_hard_state
from madis_omo import (
    ContractMadisOmoAdapter,
    MADIS_OMO_ADAPTER_VERSION,
    MADIS_OMO_SOURCE,
    MadisMinuteStatus,
    MadisOmoSourceAdapter,
)

UTC = timezone.utc


def raw_record(
    *,
    source: str = MADIS_OMO_SOURCE,
    observed: datetime | None = None,
    published: datetime | None = None,
    fetchable: datetime | None = None,
    received: datetime | None = None,
) -> RawSourceRecord:
    observed = observed or datetime(2026, 8, 18, 18, 41, tzinfo=UTC)
    published = published or datetime(2026, 8, 18, 18, 41, 4, tzinfo=UTC)
    fetchable = fetchable or datetime(2026, 8, 18, 18, 41, 5, tzinfo=UTC)
    received = received or datetime(2026, 8, 18, 18, 41, 6, tzinfo=UTC)
    return RawSourceRecord(
        record_id="raw_source_journal:9001",
        source=source,
        station_code="KLAX",
        payload_hash="abc123",
        clocks=SourceClocks(
            observed_at=observed,
            source_published_at=published,
            first_fetchable_at=fetchable,
            mercury_received_at=received,
            mercury_interpreted_at=None,
        ),
        transport="ldm",
        sequence_key="conn-7:1002",
    )


def parse(record: RawSourceRecord | None = None, **field_overrides):
    adapter = ContractMadisOmoAdapter()
    fields = {
        "station_code": "KLAX",
        "observed_at": datetime(2026, 8, 18, 18, 41, tzinfo=UTC),
        "temperature": "25.37",
        "temperature_unit": "degC",
        "upstream_variable": "temperature",
        "qc_status": "accepted",
    }
    fields.update(field_overrides)
    return adapter.parse_minute(
        raw_record=record or raw_record(),
        station_timezone="America/Los_Angeles",
        mercury_interpreted_at=datetime(2026, 8, 18, 18, 41, 6, 250000, tzinfo=UTC),
        fields=fields,
    )


class MadisOmoAdapterContractTests(unittest.TestCase):
    def test_adapter_satisfies_explicit_source_protocol(self) -> None:
        adapter = ContractMadisOmoAdapter()
        self.assertIsInstance(adapter, MadisOmoSourceAdapter)
        self.assertEqual(adapter.adapter_version, MADIS_OMO_ADAPTER_VERSION)

    def test_minute_preserves_all_available_information_clocks(self) -> None:
        result = parse()
        self.assertTrue(result.accepted_for_research)
        minute = result.minute
        self.assertEqual(minute.observed_at, datetime(2026, 8, 18, 18, 41, tzinfo=UTC))
        self.assertEqual(minute.source_published_at, datetime(2026, 8, 18, 18, 41, 4, tzinfo=UTC))
        self.assertEqual(minute.first_fetchable_at, datetime(2026, 8, 18, 18, 41, 5, tzinfo=UTC))
        self.assertEqual(minute.ldm_received_at, datetime(2026, 8, 18, 18, 41, 6, tzinfo=UTC))
        self.assertEqual(minute.mercury_interpreted_at, datetime(2026, 8, 18, 18, 41, 6, 250000, tzinfo=UTC))
        self.assertEqual(minute.metadata["observation_to_ldm_ms"], 6000)
        self.assertEqual(minute.metadata["source_release_to_ldm_ms"], 2000)
        self.assertEqual(minute.metadata["first_fetchable_to_ldm_ms"], 1000)
        self.assertEqual(minute.metadata["ldm_to_interpretation_ms"], 250)

    def test_raw_minute_is_research_only_and_cannot_raise_hard_state(self) -> None:
        result = parse()
        evidence = result.minute.to_research_evidence()
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertFalse(evidence.benchmark_eligible)
        self.assertIsNone(evidence.proven_min_f)
        timeline = accumulate_hard_state(
            [evidence],
            station_code="KLAX",
            climate_date=date(2026, 8, 18),
        )
        self.assertIsNone(timeline.current_state)
        self.assertEqual(len(timeline.applications), 1)
        self.assertEqual(timeline.applications[0].status, ApplicationStatus.REJECTED)
        self.assertEqual(timeline.applications[0].reason, "not_benchmark_eligible")

    def test_adapter_is_deterministic_for_same_raw_record_and_version(self) -> None:
        first = parse()
        second = parse()
        self.assertEqual(first, second)
        self.assertEqual(first.minute.minute_id, second.minute.minute_id)

    def test_source_wire_unit_is_preserved_not_silently_converted(self) -> None:
        result = parse(temperature="25.37", temperature_unit="degC")
        observation = result.minute.to_normalized_observation()
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(observation.value, Decimal("25.37"))
        self.assertEqual(observation.unit, "degC")

    def test_unknown_unit_fails_closed(self) -> None:
        result = parse(temperature_unit="kelvin")
        self.assertEqual(result.minute.status, MadisMinuteStatus.INVALID_UNIT)
        self.assertFalse(result.accepted_for_research)
        self.assertIsNone(result.minute.to_normalized_observation())

    def test_explicit_bad_qc_fails_closed(self) -> None:
        result = parse(qc_status="bad")
        self.assertEqual(result.minute.status, MadisMinuteStatus.QC_REJECTED)
        self.assertFalse(result.accepted_for_research)

    def test_missing_temperature_fails_closed(self) -> None:
        result = parse(temperature=None)
        self.assertEqual(result.minute.status, MadisMinuteStatus.INCOMPLETE)
        self.assertFalse(result.accepted_for_research)

    def test_receipt_before_observation_is_preserved_as_clock_skew_not_repaired(self) -> None:
        observed = datetime(2026, 8, 18, 18, 41, 10, tzinfo=UTC)
        received = datetime(2026, 8, 18, 18, 41, 6, tzinfo=UTC)
        record = raw_record(observed=observed, received=received)
        result = parse(record, observed_at=observed)
        self.assertEqual(result.minute.status, MadisMinuteStatus.CLOCK_SKEW)
        self.assertEqual(result.fail_closed_reason, "ldm_receipt_precedes_observation_clock")
        self.assertFalse(result.accepted_for_research)

    def test_lax_dst_observation_maps_to_lst_climate_date(self) -> None:
        # 00:30 PDT Aug 19 is 23:30 LST Aug 18.
        observed = datetime(2026, 8, 19, 7, 30, tzinfo=UTC)
        received = datetime(2026, 8, 19, 7, 30, 5, tzinfo=UTC)
        record = raw_record(observed=observed, received=received, published=observed, fetchable=observed)
        adapter = ContractMadisOmoAdapter()
        result = adapter.parse_minute(
            raw_record=record,
            station_timezone="America/Los_Angeles",
            mercury_interpreted_at=datetime(2026, 8, 19, 7, 30, 5, 100000, tzinfo=UTC),
            fields={
                "station_code": "KLAX",
                "observed_at": observed,
                "temperature": "20.0",
                "temperature_unit": "degC",
                "upstream_variable": "temperature",
            },
        )
        self.assertEqual(result.minute.climate_date, date(2026, 8, 18))

    def test_wrong_raw_source_fails_closed(self) -> None:
        result = parse(raw_record(source="NOAA_AWC"))
        self.assertEqual(result.minute.status, MadisMinuteStatus.INVALID_SOURCE)
        self.assertFalse(result.benchmark_eligible)


if __name__ == "__main__":
    unittest.main()
