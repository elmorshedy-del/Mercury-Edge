from __future__ import annotations

"""Pure Step 4G-C empirical validation primitives for MADIS OMO.

This module deliberately does not collect data, promote trust, eliminate Kalshi
buckets, or execute trades. It evaluates explicit MADIS storage-policy
hypotheses and compares already-decoded OMO research evidence with authoritative
comparison evidence while preserving coverage and causality limitations.

Archive compatibility, source-state agreement, live causality, and benchmark
promotion are separate claims. Nothing returned here is benchmark authority.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from math import ceil
from typing import Iterable, Sequence

from hard_information_domain import EvidenceType, SettlementEvidence
from madis_omo import MadisMinuteStatus, MadisOmoMinute
from madis_temperature_mapping import (
    MadisKelvinEncodingPolicy,
    forward_encode_f,
    inverse_candidates_f,
)

MADIS_VALIDATION_MODEL_VERSION = "madis-empirical-validation-v1"


class DataOrigin(str, Enum):
    LIVE_CAPTURE = "live_capture"
    ARCHIVE_ONLY = "archive_only"


class PolicyIdentificationStatus(str, Enum):
    IDENTIFIED = "identified"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"
    NO_QUALIFYING_SAMPLES = "no_qualifying_samples"


class CurrentComparisonStatus(str, Enum):
    MATCH = "match"
    CONTRADICTION = "contradiction"
    NOT_COMPARABLE = "not_comparable"


class CoverageStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


class MaxComparisonStatus(str, Enum):
    MATCH = "match"
    BELOW_WITH_INCOMPLETE_COVERAGE = "below_with_incomplete_coverage"
    CONTRADICTION_ABOVE_AUTHORITY = "contradiction_above_authority"
    CONTRADICTION_COMPLETE_MISMATCH = "contradiction_complete_mismatch"
    NO_OMO_DATA = "no_omo_data"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class StorageCalibrationSample:
    sample_id: str
    station_code: str
    climate_date: date
    observed_at: datetime
    observed_kelvin: Decimal
    canonical_f: int
    raw_source_ids: tuple[str, ...]
    data_origin: DataOrigin


@dataclass(frozen=True)
class PolicyEvaluation:
    policy_version: str
    resolution_k: Decimal
    rounding_rule: str
    matched_sample_ids: tuple[str, ...]
    mismatched_sample_ids: tuple[str, ...]
    ambiguous_inverse_sample_ids: tuple[str, ...]
    no_candidate_sample_ids: tuple[str, ...]
    compatible_with_all_samples: bool
    uniquely_decodable_for_all_samples: bool
    validation_model_version: str = MADIS_VALIDATION_MODEL_VERSION


@dataclass(frozen=True)
class PolicyIdentification:
    status: PolicyIdentificationStatus
    evaluations: tuple[PolicyEvaluation, ...]
    compatible_policy_versions: tuple[str, ...]
    identified_policy_version: str | None
    identified_policy_uniquely_decodable: bool
    live_capture_sample_count: int
    archive_only_sample_count: int
    establishes_live_causality: bool = False
    validation_model_version: str = MADIS_VALIDATION_MODEL_VERSION


@dataclass(frozen=True)
class CurrentComparison:
    status: CurrentComparisonStatus
    station_code: str | None
    climate_date: date | None
    observed_at: datetime | None
    omo_evidence_id: str
    authoritative_evidence_id: str
    omo_f: int | None
    authoritative_f: int | None
    reason: str | None
    source_record_ids: tuple[str, ...]
    validation_model_version: str = MADIS_VALIDATION_MODEL_VERSION


@dataclass(frozen=True)
class MaxComparison:
    status: MaxComparisonStatus
    station_code: str | None
    climate_date: date | None
    coverage_status: CoverageStatus
    omo_max_f: int | None
    authoritative_max_f: int | None
    authoritative_evidence_id: str
    omo_evidence_ids: tuple[str, ...]
    reason: str | None
    source_record_ids: tuple[str, ...]
    validation_model_version: str = MADIS_VALIDATION_MODEL_VERSION


@dataclass(frozen=True)
class LatencySummary:
    count: int
    min_ms: int | None
    median_ms: int | None
    p95_ms: int | None
    max_ms: int | None


@dataclass(frozen=True)
class LiveQualityMetrics:
    total_records: int
    unique_record_ids: int
    exact_duplicate_inputs: int
    conflicting_observation_minutes: int
    qc_rejected_records: int
    sensor_status_unverified_records: int
    clock_skew_records: int
    expected_observation_minutes: int | None
    missing_observation_minutes: int | None
    observation_to_receipt_latency: LatencySummary
    validation_model_version: str = MADIS_VALIDATION_MODEL_VERSION


def evaluate_storage_policy(
    policy: MadisKelvinEncodingPolicy,
    samples: Sequence[StorageCalibrationSample],
) -> PolicyEvaluation:
    """Evaluate one explicit storage policy against known canonical pairs."""
    matched: list[str] = []
    mismatched: list[str] = []
    ambiguous: list[str] = []
    no_candidate: list[str] = []

    for sample in samples:
        predicted = forward_encode_f(sample.canonical_f, policy)
        if predicted != sample.observed_kelvin:
            mismatched.append(sample.sample_id)
            continue

        matched.append(sample.sample_id)
        candidates = inverse_candidates_f(sample.observed_kelvin, policy)
        if not candidates or sample.canonical_f not in candidates:
            no_candidate.append(sample.sample_id)
        elif len(candidates) != 1:
            ambiguous.append(sample.sample_id)

    compatible = bool(samples) and not mismatched and not no_candidate
    uniquely_decodable = compatible and not ambiguous
    return PolicyEvaluation(
        policy_version=policy.policy_version,
        resolution_k=policy.resolution_k,
        rounding_rule=policy.rounding_rule.value,
        matched_sample_ids=tuple(matched),
        mismatched_sample_ids=tuple(mismatched),
        ambiguous_inverse_sample_ids=tuple(ambiguous),
        no_candidate_sample_ids=tuple(no_candidate),
        compatible_with_all_samples=compatible,
        uniquely_decodable_for_all_samples=uniquely_decodable,
    )


def identify_storage_policy(
    policies: Sequence[MadisKelvinEncodingPolicy],
    samples: Sequence[StorageCalibrationSample],
) -> PolicyIdentification:
    """Identify a policy only from the explicitly supplied candidate set.

    The function never searches or invents a Kelvin resolution/rounding rule.
    Archive samples may establish representation compatibility but the returned
    object always states that this alone does not establish live causality.
    """
    evaluations = tuple(evaluate_storage_policy(policy, samples) for policy in policies)
    compatible = tuple(
        item.policy_version for item in evaluations if item.compatible_with_all_samples
    )

    if not samples:
        status = PolicyIdentificationStatus.NO_QUALIFYING_SAMPLES
        identified = None
    elif len(compatible) == 1:
        status = PolicyIdentificationStatus.IDENTIFIED
        identified = compatible[0]
    elif len(compatible) > 1:
        status = PolicyIdentificationStatus.AMBIGUOUS
        identified = None
    else:
        status = PolicyIdentificationStatus.REJECTED
        identified = None

    identified_unique = False
    if identified is not None:
        identified_unique = next(
            item.uniquely_decodable_for_all_samples
            for item in evaluations
            if item.policy_version == identified
        )

    return PolicyIdentification(
        status=status,
        evaluations=evaluations,
        compatible_policy_versions=compatible,
        identified_policy_version=identified,
        identified_policy_uniquely_decodable=identified_unique,
        live_capture_sample_count=sum(1 for sample in samples if sample.data_origin is DataOrigin.LIVE_CAPTURE),
        archive_only_sample_count=sum(1 for sample in samples if sample.data_origin is DataOrigin.ARCHIVE_ONLY),
    )


def compare_aligned_current(
    omo: SettlementEvidence,
    precise_asos: SettlementEvidence,
) -> CurrentComparison:
    """Compare direct OMO state with a precise T-group at the exact same minute."""
    source_ids = tuple(dict.fromkeys((*omo.source_record_ids, *precise_asos.source_record_ids)))
    base = dict(
        omo_evidence_id=omo.evidence_id,
        authoritative_evidence_id=precise_asos.evidence_id,
        source_record_ids=source_ids,
    )

    if omo.evidence_type is not EvidenceType.MADIS_RECONSTRUCTED_5M:
        return CurrentComparison(CurrentComparisonStatus.NOT_COMPARABLE, None, None, None, **base, omo_f=None, authoritative_f=None, reason="omo_evidence_type_mismatch")
    if precise_asos.evidence_type is not EvidenceType.ASOS_T_GROUP_CURRENT:
        return CurrentComparison(CurrentComparisonStatus.NOT_COMPARABLE, None, None, None, **base, omo_f=None, authoritative_f=None, reason="authoritative_evidence_is_not_precise_t_group")
    if omo.station_code != precise_asos.station_code or omo.climate_date != precise_asos.climate_date:
        return CurrentComparison(CurrentComparisonStatus.NOT_COMPARABLE, None, None, None, **base, omo_f=_exact_f(omo), authoritative_f=_exact_f(precise_asos), reason="station_or_climate_date_mismatch")
    if omo.clocks.observed_at != precise_asos.clocks.observed_at:
        return CurrentComparison(CurrentComparisonStatus.NOT_COMPARABLE, omo.station_code, omo.climate_date, None, **base, omo_f=_exact_f(omo), authoritative_f=_exact_f(precise_asos), reason="physical_observation_minute_not_exactly_aligned")

    omo_f = _exact_f(omo)
    authority_f = _exact_f(precise_asos)
    if omo_f is None or authority_f is None:
        return CurrentComparison(CurrentComparisonStatus.NOT_COMPARABLE, omo.station_code, omo.climate_date, omo.clocks.observed_at, **base, omo_f=omo_f, authoritative_f=authority_f, reason="comparison_requires_exact_canonical_states")

    status = CurrentComparisonStatus.MATCH if omo_f == authority_f else CurrentComparisonStatus.CONTRADICTION
    return CurrentComparison(
        status=status,
        station_code=omo.station_code,
        climate_date=omo.climate_date,
        observed_at=omo.clocks.observed_at,
        omo_f=omo_f,
        authoritative_f=authority_f,
        reason=None if status is CurrentComparisonStatus.MATCH else "decoded_omo_disagrees_with_precise_asos_state",
        **base,
    )


def compare_maximum(
    omo_evidence: Iterable[SettlementEvidence],
    authoritative: SettlementEvidence,
    *,
    coverage_status: CoverageStatus,
) -> MaxComparison:
    """Compare OMO max with authoritative max without inventing missing data."""
    items = tuple(omo_evidence)
    source_ids = tuple(dict.fromkeys((
        *(source_id for item in items for source_id in item.source_record_ids),
        *authoritative.source_record_ids,
    )))
    base = dict(
        coverage_status=coverage_status,
        authoritative_evidence_id=authoritative.evidence_id,
        omo_evidence_ids=tuple(item.evidence_id for item in items),
        source_record_ids=source_ids,
    )

    exact_authority = _exact_f(authoritative)
    if exact_authority is None:
        return MaxComparison(MaxComparisonStatus.NOT_COMPARABLE, authoritative.station_code, authoritative.climate_date, **base, omo_max_f=None, authoritative_max_f=None, reason="authoritative_max_is_not_exact")

    usable_values: list[int] = []
    for item in items:
        if item.evidence_type is not EvidenceType.MADIS_RECONSTRUCTED_5M:
            return MaxComparison(MaxComparisonStatus.NOT_COMPARABLE, authoritative.station_code, authoritative.climate_date, **base, omo_max_f=None, authoritative_max_f=exact_authority, reason="non_omo_evidence_in_omo_window")
        if item.station_code != authoritative.station_code or item.climate_date != authoritative.climate_date:
            return MaxComparison(MaxComparisonStatus.NOT_COMPARABLE, authoritative.station_code, authoritative.climate_date, **base, omo_max_f=None, authoritative_max_f=exact_authority, reason="station_or_climate_date_mismatch")
        value = _exact_f(item)
        if value is None:
            return MaxComparison(MaxComparisonStatus.NOT_COMPARABLE, authoritative.station_code, authoritative.climate_date, **base, omo_max_f=None, authoritative_max_f=exact_authority, reason="omo_window_contains_non_exact_state")
        usable_values.append(value)

    if not usable_values:
        return MaxComparison(MaxComparisonStatus.NO_OMO_DATA, authoritative.station_code, authoritative.climate_date, **base, omo_max_f=None, authoritative_max_f=exact_authority, reason="no_omo_states_in_comparison_window")

    omo_max = max(usable_values)
    if omo_max == exact_authority:
        return MaxComparison(MaxComparisonStatus.MATCH, authoritative.station_code, authoritative.climate_date, **base, omo_max_f=omo_max, authoritative_max_f=exact_authority, reason=None)

    if coverage_status is CoverageStatus.COMPLETE:
        return MaxComparison(MaxComparisonStatus.CONTRADICTION_COMPLETE_MISMATCH, authoritative.station_code, authoritative.climate_date, **base, omo_max_f=omo_max, authoritative_max_f=exact_authority, reason="complete_omo_coverage_max_must_equal_authoritative_max")

    if omo_max > exact_authority:
        return MaxComparison(MaxComparisonStatus.CONTRADICTION_ABOVE_AUTHORITY, authoritative.station_code, authoritative.climate_date, **base, omo_max_f=omo_max, authoritative_max_f=exact_authority, reason="omo_max_exceeds_authoritative_max_even_with_incomplete_coverage")

    return MaxComparison(MaxComparisonStatus.BELOW_WITH_INCOMPLETE_COVERAGE, authoritative.station_code, authoritative.climate_date, **base, omo_max_f=omo_max, authoritative_max_f=exact_authority, reason="missing_omo_states_can_hide_authoritative_peak")


def assess_live_quality(
    minutes: Sequence[MadisOmoMinute],
    *,
    expected_observation_keys: set[tuple[str, date, datetime]] | None = None,
) -> LiveQualityMetrics:
    """Summarize actual capture quality without silently inventing expectations.

    Missing-minute counts are calculated only when the caller supplies the exact
    expected station/date/observation timestamps for the qualified interval.
    """
    record_ids = [minute.minute_id for minute in minutes]
    unique_ids = set(record_ids)
    exact_duplicate_inputs = len(record_ids) - len(unique_ids)

    groups: dict[tuple[str, date, datetime], set[Decimal | None]] = {}
    for minute in minutes:
        key = (minute.station_code, minute.climate_date, minute.observed_at)
        groups.setdefault(key, set()).add(minute.temperature)
    conflicts = sum(1 for values in groups.values() if len(values) > 1)

    observed_keys = set(groups)
    if expected_observation_keys is None:
        expected_count = None
        missing_count = None
    else:
        expected_count = len(expected_observation_keys)
        missing_count = len(expected_observation_keys - observed_keys)

    latency_values = [
        int((minute.ldm_received_at - minute.observed_at).total_seconds() * 1000)
        for minute in minutes
        if minute.ldm_received_at >= minute.observed_at
    ]

    return LiveQualityMetrics(
        total_records=len(minutes),
        unique_record_ids=len(unique_ids),
        exact_duplicate_inputs=exact_duplicate_inputs,
        conflicting_observation_minutes=conflicts,
        qc_rejected_records=sum(1 for minute in minutes if minute.status is MadisMinuteStatus.QC_REJECTED),
        sensor_status_unverified_records=sum(1 for minute in minutes if not minute.sensor_status_verified),
        clock_skew_records=sum(1 for minute in minutes if minute.status is MadisMinuteStatus.CLOCK_SKEW),
        expected_observation_minutes=expected_count,
        missing_observation_minutes=missing_count,
        observation_to_receipt_latency=_latency_summary(latency_values),
    )


def _exact_f(evidence: SettlementEvidence) -> int | None:
    if evidence.proven_min_f is None or evidence.proven_max_f is None:
        return None
    if evidence.proven_min_f != evidence.proven_max_f:
        return None
    return int(evidence.proven_min_f)


def _latency_summary(values: Sequence[int]) -> LatencySummary:
    if not values:
        return LatencySummary(0, None, None, None, None)
    ordered = sorted(values)
    n = len(ordered)
    # Deterministic lower median and standard nearest-rank p95.
    median = ordered[(n - 1) // 2]
    p95 = ordered[max(0, ceil(0.95 * n) - 1)]
    return LatencySummary(
        count=n,
        min_ms=ordered[0],
        median_ms=median,
        p95_ms=p95,
        max_ms=ordered[-1],
    )
