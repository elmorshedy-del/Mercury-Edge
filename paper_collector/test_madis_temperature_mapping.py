from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from hard_information_domain import EvidenceTrust
from hard_state_accumulator import accumulate_hard_state
from information_visibility import build_information_view
from madis_omo import MadisMinuteStatus, MadisOmoMinute
from madis_temperature_mapping import (
    DirectOmoClimateStatus,
    MadisKelvinEncodingPolicy,
    MinuteMappingStatus,
    SourceRoundingRule,
    derive_direct_omo_batch,
    derive_direct_omo_climate_evidence,
    forward_encode_f,
    inverse_candidates_f,
    map_madis_minute,
    omo_kelvin_before_madis_storage,
)
from market_calendar import CLIMATE_CALENDAR_VERSION

UTC = timezone.utc
DAY = date(2026, 8, 18)


def policy(
    resolution: str = "0.01",
    *,
    version: str = "synthetic-policy-v1",
    rounding: SourceRoundingRule = SourceRoundingRule.HALF_UP,
) -> MadisKelvinEncodingPolicy:
    return MadisKelvinEncodingPolicy(
        resolution_k=Decimal(resolution),
        rounding_rule=rounding,
        min_f=-100,
        max_f=140,
        policy_version=version,
    )


def minute(
    kelvin: Decimal,
    *,
    minute_id: str = "madis-minute:1",
    raw_record_id: str = "raw_source_journal:1",
    observed: datetime | None = None,
    received: datetime | None = None,
    interpreted: datetime | None = None,
    station: str = "KLAX",
    climate_day: date = DAY,
    tss: int | None = 0,
    status: MadisMinuteStatus = MadisMinuteStatus.ACCEPTED_RESEARCH,
) -> MadisOmoMinute:
    observed = observed or datetime(2026, 8, 18, 18, 41, tzinfo=UTC)
    received = received or (observed.replace(second=0, microsecond=0) if observed.second else observed)
    if received <= observed:
        received = observed.replace(microsecond=0) if observed.microsecond else observed
        received = datetime.fromtimestamp(received.timestamp() + 6, tz=UTC)
    interpreted = interpreted or datetime.fromtimestamp(received.timestamp() + 0.25, tz=UTC)
    return MadisOmoMinute(
        minute_id=minute_id,
        raw_record_id=raw_record_id,
        station_code=station,
        climate_date=climate_day,
        observed_at=observed,
        source_published_at=datetime.fromtimestamp(observed.timestamp() + 4, tz=UTC),
        first_fetchable_at=datetime.fromtimestamp(observed.timestamp() + 5, tz=UTC),
        ldm_received_at=received,
        mercury_interpreted_at=interpreted,
        temperature=kelvin,
        temperature_unit="K",
        upstream_variable="T",
        temperature_sensor_status=tss,
        qc_status=None,
        sequence_key=f"c:{minute_id.split(':')[-1]}",
        status=status,
        parser_version="madis-omo-adapter-contract-v2",
        calendar_version=CLIMATE_CALENDAR_VERSION,
        metadata={"raw_payload_hash": f"hash-{raw_record_id}"},
    )


class MadisKelvinInverseLatticeTests(unittest.TestCase):
    def test_forward_model_includes_documented_asos_tenths_c_omo_encoding(self) -> None:
        p = policy("0.01")
        # 88F -> ASOS OMO 31.1C -> 304.25K. Direct physical F->K would be
        # ~304.261K and is intentionally NOT the source model.
        self.assertEqual(omo_kelvin_before_madis_storage(88), Decimal("304.25"))
        encoded = forward_encode_f(88, p)
        self.assertEqual(encoded, Decimal("304.25"))
        self.assertEqual(inverse_candidates_f(encoded, p), (88,))

    def test_unique_synthetic_lattice_point_returns_one_canonical_f(self) -> None:
        p = policy("0.01")
        encoded = forward_encode_f(88, p)
        result = map_madis_minute(minute(encoded), p)
        self.assertEqual(result.status, MinuteMappingStatus.UNIQUE_RESEARCH)
        self.assertEqual(result.unique_f, 88)
        self.assertTrue(result.climate_state_usable)
        self.assertTrue(result.reconstruction_usable)
        self.assertFalse(result.to_dict()["second_rolling_average_applied"])
        self.assertFalse(result.to_dict()["benchmark_eligible"])

    def test_coarse_policy_can_be_ambiguous_and_is_not_climate_state_usable(self) -> None:
        p = policy("1")
        encoded = forward_encode_f(88, p)
        candidates = inverse_candidates_f(encoded, p)
        self.assertGreater(len(candidates), 1)
        result = map_madis_minute(minute(encoded), p)
        self.assertEqual(result.status, MinuteMappingStatus.AMBIGUOUS)
        self.assertEqual(result.candidates_f, candidates)
        self.assertIsNone(result.unique_f)
        self.assertFalse(result.climate_state_usable)

    def test_off_policy_kelvin_value_fails_closed_instead_of_rounding_to_f(self) -> None:
        p = policy("0.01")
        result = map_madis_minute(minute(Decimal("304.251")), p)
        self.assertEqual(result.status, MinuteMappingStatus.OFF_POLICY_VALUE)
        self.assertIsNone(result.unique_f)
        self.assertEqual(result.fail_closed_reason, "kelvin_value_not_on_configured_source_lattice")

    def test_unknown_source_encoding_policy_fails_closed(self) -> None:
        result = map_madis_minute(minute(Decimal("304.25")), None)
        self.assertEqual(result.status, MinuteMappingStatus.UNVERIFIED_SOURCE_ENCODING)
        self.assertFalse(result.climate_state_usable)

    def test_bad_upstream_omo_record_cannot_be_mapped_even_on_valid_lattice(self) -> None:
        p = policy()
        encoded = forward_encode_f(77, p)
        result = map_madis_minute(minute(encoded, status=MadisMinuteStatus.QC_REJECTED), p)
        self.assertEqual(result.status, MinuteMappingStatus.SOURCE_NOT_RESEARCH_USABLE)
        self.assertFalse(result.climate_state_usable)

    def test_policy_version_changes_derivation_identity(self) -> None:
        encoded = forward_encode_f(88, policy(version="p1"))
        first = map_madis_minute(minute(encoded), policy(version="p1"))
        second = map_madis_minute(minute(encoded), policy(version="p2"))
        self.assertNotEqual(first.derivation_id, second.derivation_id)
        self.assertEqual(first.unique_f, second.unique_f)

    def test_rounding_rule_is_part_of_explicit_storage_policy_identity(self) -> None:
        p1 = policy(version="same", rounding=SourceRoundingRule.HALF_UP)
        p2 = policy(version="same", rounding=SourceRoundingRule.HALF_EVEN)
        encoded = forward_encode_f(77, p1)
        first = map_madis_minute(minute(encoded), p1)
        second = map_madis_minute(minute(encoded), p2)
        self.assertNotEqual(first.derivation_id, second.derivation_id)

    def test_mapping_is_deterministic(self) -> None:
        p = policy()
        m = minute(forward_encode_f(89, p))
        self.assertEqual(map_madis_minute(m, p), map_madis_minute(m, p))

    def test_invalid_policy_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            policy("0")
        with self.assertRaises(ValueError):
            MadisKelvinEncodingPolicy(
                resolution_k=Decimal("0.01"),
                rounding_rule=SourceRoundingRule.HALF_UP,
                min_f=100,
                max_f=0,
                policy_version="x",
            )


class DirectOmoClimateEvidenceTests(unittest.TestCase):
    def test_one_unique_omo_mapping_creates_research_only_five_minute_state(self) -> None:
        p = policy()
        m = minute(forward_encode_f(88, p))
        mapping = map_madis_minute(m, p)
        result = derive_direct_omo_climate_evidence(m, mapping)
        self.assertEqual(result.status, DirectOmoClimateStatus.RESEARCH_EVIDENCE)
        self.assertEqual(result.mapped_f, 88)
        self.assertIsNotNone(result.evidence)
        assert result.evidence is not None
        self.assertEqual(result.evidence.proven_min_f, 88)
        self.assertEqual(result.evidence.possible_canonical_f, (88,))
        self.assertEqual(result.evidence.trust, EvidenceTrust.RESEARCH_ONLY)
        self.assertFalse(result.evidence.benchmark_eligible)
        self.assertTrue(result.evidence.metadata["direct_omo_climate_state"])
        self.assertFalse(result.evidence.metadata["second_rolling_average_applied"])

    def test_direct_omo_research_evidence_cannot_change_benchmark_hard_state(self) -> None:
        p = policy()
        m = minute(forward_encode_f(88, p))
        result = derive_direct_omo_climate_evidence(m, map_madis_minute(m, p))
        assert result.evidence is not None
        timeline = accumulate_hard_state(
            [result.evidence],
            station_code="KLAX",
            climate_date=DAY,
        )
        self.assertIsNone(timeline.current_state)

    def test_no_second_rolling_average_is_applied_to_omo_records(self) -> None:
        p = policy()
        base = datetime(2026, 8, 18, 18, 40, tzinfo=UTC)
        values = [80, 90, 80]
        minutes = [
            minute(
                forward_encode_f(value, p),
                minute_id=f"madis-minute:{index}",
                raw_record_id=f"raw:{index}",
                observed=datetime.fromtimestamp(base.timestamp() + index * 60, tz=UTC),
            )
            for index, value in enumerate(values)
        ]
        batch = derive_direct_omo_batch(minutes, p)
        self.assertEqual(tuple(item.proven_min_f for item in batch.evidence), (80, 90, 80))

    def test_missing_observation_minute_creates_no_interpolated_state(self) -> None:
        p = policy()
        first_obs = datetime(2026, 8, 18, 18, 41, tzinfo=UTC)
        third_obs = datetime(2026, 8, 18, 18, 43, tzinfo=UTC)
        first = minute(
            forward_encode_f(85, p),
            minute_id="madis-minute:first",
            raw_record_id="raw:first",
            observed=first_obs,
        )
        third = minute(
            forward_encode_f(87, p),
            minute_id="madis-minute:third",
            raw_record_id="raw:third",
            observed=third_obs,
        )
        batch = derive_direct_omo_batch([first, third], p)
        self.assertEqual(len(batch.evidence), 2)
        self.assertEqual(
            tuple(item.clocks.observed_at for item in batch.evidence),
            (first_obs, third_obs),
        )

    def test_late_old_observation_is_not_backdated_to_physical_time(self) -> None:
        p = policy()
        observed = datetime(2026, 8, 18, 18, 0, tzinfo=UTC)
        received = datetime(2026, 8, 18, 19, 0, tzinfo=UTC)
        interpreted = datetime(2026, 8, 18, 19, 0, 1, tzinfo=UTC)
        late = minute(
            forward_encode_f(88, p),
            minute_id="madis-minute:late",
            raw_record_id="raw:late",
            observed=observed,
            received=received,
            interpreted=interpreted,
        )
        batch = derive_direct_omo_batch([late], p)
        self.assertEqual(batch.results[0].mercury_known_at, interpreted)
        before = build_information_view(
            batch.evidence,
            station_code="KLAX",
            climate_date=DAY,
            as_of=datetime(2026, 8, 18, 18, 30, tzinfo=UTC),
        )
        after = build_information_view(
            batch.evidence,
            station_code="KLAX",
            climate_date=DAY,
            as_of=datetime(2026, 8, 18, 19, 1, tzinfo=UTC),
        )
        self.assertIsNone(before.mercury_research_high_min_f)
        self.assertEqual(after.mercury_research_high_min_f, 88)

    def test_later_lower_omo_state_does_not_reduce_research_daily_high_bound(self) -> None:
        p = policy()
        high_obs = datetime(2026, 8, 18, 18, 0, tzinfo=UTC)
        low_obs = datetime(2026, 8, 18, 19, 0, tzinfo=UTC)
        high = minute(
            forward_encode_f(88, p),
            minute_id="madis-minute:high",
            raw_record_id="raw:high",
            observed=high_obs,
        )
        low = minute(
            forward_encode_f(84, p),
            minute_id="madis-minute:low",
            raw_record_id="raw:low",
            observed=low_obs,
        )
        batch = derive_direct_omo_batch([high, low], p)
        view = build_information_view(
            batch.evidence,
            station_code="KLAX",
            climate_date=DAY,
            as_of=datetime(2026, 8, 18, 20, 0, tzinfo=UTC),
        )
        self.assertEqual(view.mercury_research_high_min_f, 88)

    def test_missing_tss_cannot_produce_direct_climate_state(self) -> None:
        p = policy()
        m = minute(forward_encode_f(88, p), tss=None)
        result = derive_direct_omo_climate_evidence(m, map_madis_minute(m, p))
        self.assertEqual(result.status, DirectOmoClimateStatus.SENSOR_STATUS_UNVERIFIED)
        self.assertIsNone(result.evidence)

    def test_mapping_from_different_raw_record_fails_closed(self) -> None:
        p = policy()
        first = minute(
            forward_encode_f(88, p),
            minute_id="madis-minute:first",
            raw_record_id="raw:first",
        )
        second = minute(
            forward_encode_f(88, p),
            minute_id="madis-minute:second",
            raw_record_id="raw:second",
        )
        result = derive_direct_omo_climate_evidence(second, map_madis_minute(first, p))
        self.assertEqual(result.status, DirectOmoClimateStatus.MAPPING_SOURCE_MISMATCH)
        self.assertIsNone(result.evidence)

    def test_exact_duplicate_input_is_idempotent(self) -> None:
        p = policy()
        m = minute(forward_encode_f(88, p))
        batch = derive_direct_omo_batch([m, m], p)
        self.assertEqual(len(batch.results), 1)
        self.assertEqual(len(batch.evidence), 1)
        self.assertEqual(batch, derive_direct_omo_batch([m, m], p))

    def test_conflicting_same_observation_minute_fails_closed(self) -> None:
        p = policy()
        observed = datetime(2026, 8, 18, 18, 41, tzinfo=UTC)
        one = minute(
            forward_encode_f(88, p),
            minute_id="madis-minute:one",
            raw_record_id="raw:one",
            observed=observed,
        )
        two = minute(
            forward_encode_f(89, p),
            minute_id="madis-minute:two",
            raw_record_id="raw:two",
            observed=observed,
            received=datetime(2026, 8, 18, 18, 41, 7, tzinfo=UTC),
        )
        batch = derive_direct_omo_batch([one, two], p)
        self.assertEqual(len(batch.evidence), 0)
        self.assertEqual(
            {result.status for result in batch.results},
            {DirectOmoClimateStatus.CONFLICTING_SAME_MINUTE},
        )
        self.assertEqual(
            batch.results[0].conflicting_raw_record_ids,
            ("raw:one", "raw:two"),
        )

    def test_station_and_climate_date_remain_explicit_and_filterable(self) -> None:
        p = policy()
        lax = minute(
            forward_encode_f(88, p),
            minute_id="madis-minute:lax",
            raw_record_id="raw:lax",
            station="KLAX",
            climate_day=DAY,
        )
        phl = minute(
            forward_encode_f(99, p),
            minute_id="madis-minute:phl",
            raw_record_id="raw:phl",
            station="KPHL",
            climate_day=date(2026, 8, 19),
        )
        batch = derive_direct_omo_batch([lax, phl], p)
        view = build_information_view(
            batch.evidence,
            station_code="KLAX",
            climate_date=DAY,
            as_of=datetime(2026, 8, 18, 23, 0, tzinfo=UTC),
        )
        self.assertEqual(view.mercury_research_high_min_f, 88)


if __name__ == "__main__":
    unittest.main()
