from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from hard_information_domain import (
    EvidenceTrust,
    EvidenceType,
    IntegrityStatus,
    SettlementEvidence,
    SourceClocks,
)
from madis_omo import MadisMinuteStatus, MadisOmoMinute
from madis_temperature_mapping import (
    MadisKelvinEncodingPolicy,
    SourceRoundingRule,
    forward_encode_f,
)
from madis_validation import (
    CoverageStatus,
    CurrentComparisonStatus,
    DataOrigin,
    MaxComparisonStatus,
    PolicyIdentificationStatus,
    StorageCalibrationSample,
    assess_live_quality,
    compare_aligned_current,
    compare_maximum,
    identify_storage_policy,
)
from market_calendar import CLIMATE_CALENDAR_VERSION

UTC = timezone.utc
DAY = date(2026, 8, 18)
BASE = datetime(2026, 8, 18, 18, 0, tzinfo=UTC)


def policy(
    resolution: str,
    *,
    version: str,
    rounding: SourceRoundingRule = SourceRoundingRule.HALF_UP,
) -> MadisKelvinEncodingPolicy:
    return MadisKelvinEncodingPolicy(
        resolution_k=Decimal(resolution),
        rounding_rule=rounding,
        min_f=-100,
        max_f=140,
        policy_version=version,
    )


def calibration(
    sample_id: str,
    value_f: int,
    observed_kelvin: Decimal,
    *,
    origin: DataOrigin = DataOrigin.ARCHIVE_ONLY,
) -> StorageCalibrationSample:
    return StorageCalibrationSample(
        sample_id=sample_id,
        station_code="KLAX",
        climate_date=DAY,
        observed_at=BASE,
        observed_kelvin=observed_kelvin,
        canonical_f=value_f,
        raw_source_ids=(f"raw:{sample_id}",),
        data_origin=origin,
    )


def evidence(
    evidence_id: str,
    evidence_type: EvidenceType,
    value_f: int,
    *,
    observed_at: datetime = BASE,
    station: str = "KLAX",
    climate_day: date = DAY,
    trust: EvidenceTrust = EvidenceTrust.RESEARCH_ONLY,
) -> SettlementEvidence:
    return SettlementEvidence(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        station_code=station,
        climate_date=climate_day,
        source_record_ids=(f"raw:{evidence_id}",),
        proven_min_f=value_f,
        proven_max_f=value_f,
        integrity_status=IntegrityStatus.CANONICAL,
        trust=trust,
        clocks=SourceClocks(
            observed_at=observed_at,
            source_published_at=observed_at + timedelta(seconds=2),
            first_fetchable_at=observed_at + timedelta(seconds=3),
            mercury_received_at=observed_at + timedelta(seconds=5),
            mercury_interpreted_at=observed_at + timedelta(seconds=6),
        ),
        parser_version="test-parser-v1",
        evidence_model_version="test-evidence-v1",
        calendar_version=CLIMATE_CALENDAR_VERSION,
        raw_identifier=evidence_id,
        possible_canonical_f=(value_f,),
        metadata={"source": "MADIS_OMO" if evidence_type is EvidenceType.MADIS_RECONSTRUCTED_5M else "NOAA_AWC"},
    )


def minute(
    minute_id: str,
    *,
    observed_at: datetime,
    value_k: Decimal,
    received_delay_s: int = 5,
    status: MadisMinuteStatus = MadisMinuteStatus.ACCEPTED_RESEARCH,
    tss: int | None = 0,
) -> MadisOmoMinute:
    received = observed_at + timedelta(seconds=received_delay_s)
    if status is MadisMinuteStatus.CLOCK_SKEW:
        received = observed_at - timedelta(seconds=1)
    return MadisOmoMinute(
        minute_id=minute_id,
        raw_record_id=f"raw:{minute_id}",
        station_code="KLAX",
        climate_date=DAY,
        observed_at=observed_at,
        source_published_at=observed_at + timedelta(seconds=2),
        first_fetchable_at=observed_at + timedelta(seconds=3),
        ldm_received_at=received,
        mercury_interpreted_at=received + timedelta(milliseconds=100),
        temperature=value_k,
        temperature_unit="K",
        upstream_variable="T",
        temperature_sensor_status=tss,
        qc_status="bad" if status is MadisMinuteStatus.QC_REJECTED else None,
        sequence_key=minute_id,
        status=status,
        parser_version="madis-omo-adapter-contract-v2",
        calendar_version=CLIMATE_CALENDAR_VERSION,
        metadata={"raw_payload_hash": f"hash:{minute_id}"},
    )


class StoragePolicyValidationTests(unittest.TestCase):
    def test_one_compatible_candidate_is_identified(self) -> None:
        fine = policy("0.01", version="fine")
        coarse = policy("1", version="coarse")
        samples = [calibration("s1", 88, forward_encode_f(88, fine))]
        result = identify_storage_policy([fine, coarse], samples)
        self.assertEqual(result.status, PolicyIdentificationStatus.IDENTIFIED)
        self.assertEqual(result.identified_policy_version, "fine")
        self.assertTrue(result.identified_policy_uniquely_decodable)
        self.assertFalse(result.establishes_live_causality)

    def test_two_compatible_candidates_remain_ambiguous(self) -> None:
        half_up = policy("0.01", version="half-up", rounding=SourceRoundingRule.HALF_UP)
        half_even = policy("0.01", version="half-even", rounding=SourceRoundingRule.HALF_EVEN)
        samples = [calibration("s1", 88, forward_encode_f(88, half_up))]
        result = identify_storage_policy([half_up, half_even], samples)
        self.assertEqual(result.status, PolicyIdentificationStatus.AMBIGUOUS)
        self.assertIsNone(result.identified_policy_version)
        self.assertEqual(set(result.compatible_policy_versions), {"half-up", "half-even"})

    def test_no_candidate_explains_samples_is_rejected(self) -> None:
        fine = policy("0.01", version="fine")
        coarse = policy("1", version="coarse")
        samples = [calibration("s1", 88, Decimal("304.26"))]
        result = identify_storage_policy([fine, coarse], samples)
        self.assertEqual(result.status, PolicyIdentificationStatus.REJECTED)
        self.assertEqual(result.compatible_policy_versions, ())

    def test_archive_compatibility_never_claims_live_causality(self) -> None:
        fine = policy("0.01", version="fine")
        result = identify_storage_policy(
            [fine],
            [calibration("archive", 88, forward_encode_f(88, fine), origin=DataOrigin.ARCHIVE_ONLY)],
        )
        self.assertEqual(result.archive_only_sample_count, 1)
        self.assertEqual(result.live_capture_sample_count, 0)
        self.assertFalse(result.establishes_live_causality)


class CurrentAgreementTests(unittest.TestCase):
    def test_exact_minute_omo_and_t_group_agree(self) -> None:
        omo = evidence("omo", EvidenceType.MADIS_RECONSTRUCTED_5M, 88)
        precise = evidence("t", EvidenceType.ASOS_T_GROUP_CURRENT, 88, trust=EvidenceTrust.BENCHMARK_ELIGIBLE)
        result = compare_aligned_current(omo, precise)
        self.assertEqual(result.status, CurrentComparisonStatus.MATCH)
        self.assertEqual(result.omo_f, 88)
        self.assertEqual(result.authoritative_f, 88)

    def test_exact_minute_disagreement_is_contradiction(self) -> None:
        omo = evidence("omo", EvidenceType.MADIS_RECONSTRUCTED_5M, 88)
        precise = evidence("t", EvidenceType.ASOS_T_GROUP_CURRENT, 87, trust=EvidenceTrust.BENCHMARK_ELIGIBLE)
        result = compare_aligned_current(omo, precise)
        self.assertEqual(result.status, CurrentComparisonStatus.CONTRADICTION)
        self.assertEqual(result.reason, "decoded_omo_disagrees_with_precise_asos_state")

    def test_different_physical_minute_is_not_compared_by_magic_tolerance(self) -> None:
        omo = evidence("omo", EvidenceType.MADIS_RECONSTRUCTED_5M, 88, observed_at=BASE)
        precise = evidence("t", EvidenceType.ASOS_T_GROUP_CURRENT, 88, observed_at=BASE + timedelta(minutes=1), trust=EvidenceTrust.BENCHMARK_ELIGIBLE)
        result = compare_aligned_current(omo, precise)
        self.assertEqual(result.status, CurrentComparisonStatus.NOT_COMPARABLE)
        self.assertEqual(result.reason, "physical_observation_minute_not_exactly_aligned")


class MaximumAgreementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.omo = [
            evidence("omo85", EvidenceType.MADIS_RECONSTRUCTED_5M, 85, observed_at=BASE),
            evidence("omo88", EvidenceType.MADIS_RECONSTRUCTED_5M, 88, observed_at=BASE + timedelta(minutes=1)),
            evidence("omo86", EvidenceType.MADIS_RECONSTRUCTED_5M, 86, observed_at=BASE + timedelta(minutes=2)),
        ]

    def test_complete_coverage_equal_max_passes(self) -> None:
        authority = evidence("six88", EvidenceType.ASOS_SIX_HOUR_MAX, 88, trust=EvidenceTrust.BENCHMARK_ELIGIBLE)
        result = compare_maximum(self.omo, authority, coverage_status=CoverageStatus.COMPLETE)
        self.assertEqual(result.status, MaxComparisonStatus.MATCH)

    def test_complete_coverage_below_authority_is_contradiction(self) -> None:
        authority = evidence("six89", EvidenceType.ASOS_SIX_HOUR_MAX, 89, trust=EvidenceTrust.BENCHMARK_ELIGIBLE)
        result = compare_maximum(self.omo, authority, coverage_status=CoverageStatus.COMPLETE)
        self.assertEqual(result.status, MaxComparisonStatus.CONTRADICTION_COMPLETE_MISMATCH)

    def test_incomplete_coverage_can_explain_lower_omo_max(self) -> None:
        authority = evidence("six89", EvidenceType.ASOS_SIX_HOUR_MAX, 89, trust=EvidenceTrust.BENCHMARK_ELIGIBLE)
        result = compare_maximum(self.omo, authority, coverage_status=CoverageStatus.INCOMPLETE)
        self.assertEqual(result.status, MaxComparisonStatus.BELOW_WITH_INCOMPLETE_COVERAGE)

    def test_incomplete_coverage_cannot_explain_omo_above_authority(self) -> None:
        authority = evidence("six87", EvidenceType.ASOS_SIX_HOUR_MAX, 87, trust=EvidenceTrust.BENCHMARK_ELIGIBLE)
        result = compare_maximum(self.omo, authority, coverage_status=CoverageStatus.INCOMPLETE)
        self.assertEqual(result.status, MaxComparisonStatus.CONTRADICTION_ABOVE_AUTHORITY)


class LiveQualityTests(unittest.TestCase):
    def test_quality_metrics_preserve_duplicates_conflicts_gaps_and_latency(self) -> None:
        p = policy("0.01", version="fine")
        t0 = BASE
        t1 = BASE + timedelta(minutes=1)
        t2 = BASE + timedelta(minutes=2)
        m0 = minute("m0", observed_at=t0, value_k=forward_encode_f(85, p), received_delay_s=2)
        m0_duplicate = m0
        conflict = minute("m0-conflict", observed_at=t0, value_k=forward_encode_f(86, p), received_delay_s=3)
        rejected = minute("m1", observed_at=t1, value_k=forward_encode_f(86, p), received_delay_s=5, status=MadisMinuteStatus.QC_REJECTED, tss=None)
        skew = minute("skew", observed_at=t1, value_k=forward_encode_f(86, p), status=MadisMinuteStatus.CLOCK_SKEW)

        metrics = assess_live_quality(
            [m0, m0_duplicate, conflict, rejected, skew],
            expected_observation_keys={
                ("KLAX", DAY, t0),
                ("KLAX", DAY, t1),
                ("KLAX", DAY, t2),
            },
        )
        self.assertEqual(metrics.total_records, 5)
        self.assertEqual(metrics.unique_record_ids, 4)
        self.assertEqual(metrics.exact_duplicate_inputs, 1)
        self.assertEqual(metrics.conflicting_observation_minutes, 1)
        self.assertEqual(metrics.qc_rejected_records, 1)
        self.assertEqual(metrics.sensor_status_unverified_records, 1)
        self.assertEqual(metrics.clock_skew_records, 1)
        self.assertEqual(metrics.expected_observation_minutes, 3)
        self.assertEqual(metrics.missing_observation_minutes, 1)
        self.assertEqual(metrics.observation_to_receipt_latency.count, 4)
        self.assertEqual(metrics.observation_to_receipt_latency.min_ms, 2000)
        self.assertEqual(metrics.observation_to_receipt_latency.max_ms, 5000)


if __name__ == "__main__":
    unittest.main()
