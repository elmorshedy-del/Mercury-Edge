from __future__ import annotations

"""Research-only decoding of MADIS OMO ``T`` into ASOS whole-°F climate state.

Authoritative ASOS semantics matter here. OMO air temperature is already the
ASOS running five-minute average reported on a one-minute cadence. ASOS first
stores that running average as a whole degree Fahrenheit, then converts the
whole-°F state to the nearest 0.1 °C for OMO/METAR reporting. MADIS stores the
OMO air-temperature variable ``T`` in Kelvin.

Therefore Mercury must NOT average five MADIS OMO ``T`` records again. The
correct decoding path is:

canonical whole °F -> documented ASOS 0.1 °C OMO lattice -> Kelvin ->
versioned MADIS storage/encoding policy.

The final MADIS storage representation still has to be established empirically
for the chosen live LDM/API path. This module requires that representation as an
explicit policy and inverts the resulting lattice. Unknown, off-policy, or
ambiguous values fail closed. Even a unique result remains RESEARCH_ONLY until
validation and explicit trust promotion.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
from enum import Enum
from hashlib import sha256
from typing import Any, Iterable

from asos_evidence import canonical_f_to_tenths_c
from hard_information_domain import (
    EvidenceTrust,
    EvidenceType,
    IntegrityStatus,
    SettlementEvidence,
    SourceClocks,
)
from madis_omo import (
    MADIS_OMO_SOURCE,
    MADIS_OMO_TEMPERATURE_UNIT,
    MADIS_OMO_TEMPERATURE_VARIABLE,
    OMO_TEMPERATURE_SEMANTICS,
    MadisOmoMinute,
)

MAPPING_MODEL_VERSION = "madis-omo-kelvin-inverse-lattice-v2"
DIRECT_OMO_EVIDENCE_MODEL_VERSION = "madis-omo-direct-5m-research-v1"
KELVIN_OFFSET = Decimal("273.15")


class SourceRoundingRule(str, Enum):
    HALF_UP = "half_up"
    HALF_EVEN = "half_even"


class MinuteMappingStatus(str, Enum):
    UNIQUE_RESEARCH = "unique_research"
    AMBIGUOUS = "ambiguous"
    OFF_POLICY_VALUE = "off_policy_value"
    NO_CANDIDATE = "no_candidate"
    SOURCE_NOT_RESEARCH_USABLE = "source_not_research_usable"
    UNVERIFIED_SOURCE_ENCODING = "unverified_source_encoding"


class DirectOmoClimateStatus(str, Enum):
    RESEARCH_EVIDENCE = "research_evidence"
    MAPPING_UNUSABLE = "mapping_unusable"
    SENSOR_STATUS_UNVERIFIED = "sensor_status_unverified"
    MAPPING_SOURCE_MISMATCH = "mapping_source_mismatch"
    CONFLICTING_SAME_MINUTE = "conflicting_same_minute"


@dataclass(frozen=True)
class MadisKelvinEncodingPolicy:
    """Explicit hypothesis for MADIS storage of the already-encoded OMO value.

    `resolution_k` describes the representation Mercury receives from MADIS,
    after ASOS has already performed its whole-°F -> 0.1°C OMO conversion. It is
    not a physical sensor precision and not an ASOS averaging rule.
    """

    resolution_k: Decimal
    rounding_rule: SourceRoundingRule
    min_f: int = -150
    max_f: int = 150
    policy_version: str = "unverified-madis-omo-kelvin-storage-v1"

    def __post_init__(self) -> None:
        if self.resolution_k <= 0:
            raise ValueError("resolution_k must be positive")
        if self.min_f > self.max_f:
            raise ValueError("min_f must be <= max_f")
        if not self.policy_version.strip():
            raise ValueError("policy_version is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution_k": format(self.resolution_k, "f"),
            "rounding_rule": self.rounding_rule.value,
            "min_f": self.min_f,
            "max_f": self.max_f,
            "policy_version": self.policy_version,
            "mapping_model_version": MAPPING_MODEL_VERSION,
            "upstream_semantics": OMO_TEMPERATURE_SEMANTICS,
        }


@dataclass(frozen=True)
class CandidateMinuteF:
    """Candidate ASOS running-five-minute whole-°F state for one OMO record.

    The historical class name is retained for compatibility. `minute` means OMO
    cadence, not a raw one-minute sensor input.
    """

    derivation_id: str
    minute_id: str
    raw_record_id: str
    station_code: str
    observed_kelvin: Decimal | None
    candidates_f: tuple[int, ...]
    status: MinuteMappingStatus
    policy: MadisKelvinEncodingPolicy | None
    mapping_model_version: str = MAPPING_MODEL_VERSION
    fail_closed_reason: str | None = None

    @property
    def unique_f(self) -> int | None:
        if self.status is MinuteMappingStatus.UNIQUE_RESEARCH and len(self.candidates_f) == 1:
            return self.candidates_f[0]
        return None

    @property
    def climate_state_usable(self) -> bool:
        return self.unique_f is not None

    @property
    def reconstruction_usable(self) -> bool:
        # Compatibility alias. No second rolling reconstruction is performed.
        return self.climate_state_usable

    def to_dict(self) -> dict[str, Any]:
        return {
            "derivation_id": self.derivation_id,
            "minute_id": self.minute_id,
            "raw_record_id": self.raw_record_id,
            "station_code": self.station_code,
            "observed_kelvin": format(self.observed_kelvin, "f") if self.observed_kelvin is not None else None,
            "candidates_f": list(self.candidates_f),
            "status": self.status.value,
            "policy": self.policy.to_dict() if self.policy is not None else None,
            "mapping_model_version": self.mapping_model_version,
            "fail_closed_reason": self.fail_closed_reason,
            "omo_temperature_semantics": OMO_TEMPERATURE_SEMANTICS,
            "second_rolling_average_applied": False,
            "benchmark_eligible": False,
        }


@dataclass(frozen=True)
class DirectOmoClimateResult:
    result_id: str
    minute_id: str
    station_code: str
    climate_date: date
    observed_at: datetime
    mercury_known_at: datetime
    mapping_derivation_id: str
    mapped_f: int | None
    status: DirectOmoClimateStatus
    evidence: SettlementEvidence | None
    fail_closed_reason: str | None = None
    conflicting_raw_record_ids: tuple[str, ...] = ()

    @property
    def research_usable(self) -> bool:
        return self.status is DirectOmoClimateStatus.RESEARCH_EVIDENCE and self.evidence is not None


@dataclass(frozen=True)
class DirectOmoClimateBatch:
    results: tuple[DirectOmoClimateResult, ...]
    evidence: tuple[SettlementEvidence, ...]


def omo_kelvin_before_madis_storage(value_f: int) -> Decimal:
    """Documented ASOS OMO lattice: whole °F -> nearest 0.1°C -> Kelvin."""
    return canonical_f_to_tenths_c(value_f) + KELVIN_OFFSET


def fahrenheit_to_kelvin_exact(value_f: int) -> Decimal:
    """Compatibility alias for the documented OMO Kelvin lattice.

    This intentionally no longer means direct physical F->K conversion. The OMO
    path includes ASOS's documented nearest-0.1°C encoding before MADIS storage.
    """
    return omo_kelvin_before_madis_storage(value_f)


def quantize_increment(
    value: Decimal,
    increment: Decimal,
    rule: SourceRoundingRule,
) -> Decimal:
    rounding = ROUND_HALF_UP if rule is SourceRoundingRule.HALF_UP else ROUND_HALF_EVEN
    with localcontext() as ctx:
        ctx.prec = 50
        units = (value / increment).quantize(Decimal(1), rounding=rounding)
        return units * increment


def forward_encode_f(value_f: int, policy: MadisKelvinEncodingPolicy) -> Decimal:
    """Forward model through ASOS OMO encoding, then configured MADIS storage."""
    return quantize_increment(
        omo_kelvin_before_madis_storage(value_f),
        policy.resolution_k,
        policy.rounding_rule,
    )


def inverse_candidates_f(
    observed_kelvin: Decimal,
    policy: MadisKelvinEncodingPolicy,
) -> tuple[int, ...]:
    """Return every whole-°F OMO climate state that can yield this MADIS value."""
    if quantize_increment(observed_kelvin, policy.resolution_k, policy.rounding_rule) != observed_kelvin:
        return ()
    return tuple(
        value_f
        for value_f in range(policy.min_f, policy.max_f + 1)
        if forward_encode_f(value_f, policy) == observed_kelvin
    )


def map_madis_minute(
    minute: MadisOmoMinute,
    policy: MadisKelvinEncodingPolicy | None,
) -> CandidateMinuteF:
    """Map one accepted OMO record to an explicit inverse whole-°F candidate set."""
    observed = minute.temperature
    if policy is None:
        return _result(
            minute,
            observed,
            (),
            MinuteMappingStatus.UNVERIFIED_SOURCE_ENCODING,
            None,
            "madis_source_encoding_policy_not_verified",
        )
    if not minute.research_usable:
        return _result(
            minute,
            observed,
            (),
            MinuteMappingStatus.SOURCE_NOT_RESEARCH_USABLE,
            policy,
            "madis_omo_record_not_research_usable",
        )
    if minute.temperature_unit != MADIS_OMO_TEMPERATURE_UNIT or minute.upstream_variable != MADIS_OMO_TEMPERATURE_VARIABLE:
        return _result(
            minute,
            observed,
            (),
            MinuteMappingStatus.SOURCE_NOT_RESEARCH_USABLE,
            policy,
            "madis_temperature_contract_mismatch",
        )
    if observed is None:
        return _result(
            minute,
            None,
            (),
            MinuteMappingStatus.SOURCE_NOT_RESEARCH_USABLE,
            policy,
            "madis_temperature_missing",
        )

    normalized = quantize_increment(observed, policy.resolution_k, policy.rounding_rule)
    if normalized != observed:
        return _result(
            minute,
            observed,
            (),
            MinuteMappingStatus.OFF_POLICY_VALUE,
            policy,
            "kelvin_value_not_on_configured_source_lattice",
        )

    candidates = inverse_candidates_f(observed, policy)
    if not candidates:
        return _result(
            minute,
            observed,
            (),
            MinuteMappingStatus.NO_CANDIDATE,
            policy,
            "no_canonical_f_state_matches_configured_omo_kelvin_encoding",
        )
    if len(candidates) > 1:
        return _result(
            minute,
            observed,
            candidates,
            MinuteMappingStatus.AMBIGUOUS,
            policy,
            "configured_omo_kelvin_encoding_maps_to_multiple_canonical_f_states",
        )
    return _result(
        minute,
        observed,
        candidates,
        MinuteMappingStatus.UNIQUE_RESEARCH,
        policy,
        None,
    )


def derive_direct_omo_climate_evidence(
    minute: MadisOmoMinute,
    mapping: CandidateMinuteF,
) -> DirectOmoClimateResult:
    """Turn one uniquely decoded OMO current temperature into research evidence."""
    known_at = minute.mercury_interpreted_at or minute.ldm_received_at
    base_id_parts: tuple[Any, ...] = (
        minute.minute_id,
        mapping.derivation_id,
        DIRECT_OMO_EVIDENCE_MODEL_VERSION,
    )

    if (
        mapping.minute_id != minute.minute_id
        or mapping.raw_record_id != minute.raw_record_id
        or mapping.station_code != minute.station_code
    ):
        return _direct_result(
            minute,
            mapping,
            DirectOmoClimateStatus.MAPPING_SOURCE_MISMATCH,
            None,
            "mapping_does_not_belong_to_omo_record",
            base_id_parts,
        )
    if not mapping.climate_state_usable:
        return _direct_result(
            minute,
            mapping,
            DirectOmoClimateStatus.MAPPING_UNUSABLE,
            None,
            mapping.fail_closed_reason or "omo_kelvin_mapping_not_unique",
            base_id_parts,
        )
    if not minute.sensor_status_verified:
        return _direct_result(
            minute,
            mapping,
            DirectOmoClimateStatus.SENSOR_STATUS_UNVERIFIED,
            None,
            "temperature_sensor_status_must_be_verified_operating",
            base_id_parts,
        )

    mapped_f = mapping.unique_f
    assert mapped_f is not None
    policy = mapping.policy
    assert policy is not None
    evidence_id = _stable_id("madis-omo-5m-current", *base_id_parts)
    evidence = SettlementEvidence(
        evidence_id=evidence_id,
        # Existing canonical enum name is retained to avoid a broad schema
        # migration; v1 semantics are now explicitly direct OMO five-minute.
        evidence_type=EvidenceType.MADIS_RECONSTRUCTED_5M,
        station_code=minute.station_code,
        climate_date=minute.climate_date,
        source_record_ids=(minute.raw_record_id,),
        proven_min_f=mapped_f,
        proven_max_f=mapped_f,
        integrity_status=IntegrityStatus.CANONICAL,
        trust=EvidenceTrust.RESEARCH_ONLY,
        clocks=SourceClocks(
            observed_at=minute.observed_at,
            source_published_at=minute.source_published_at,
            first_fetchable_at=minute.first_fetchable_at,
            mercury_received_at=minute.ldm_received_at,
            mercury_interpreted_at=minute.mercury_interpreted_at,
        ),
        parser_version=minute.parser_version,
        evidence_model_version=DIRECT_OMO_EVIDENCE_MODEL_VERSION,
        calendar_version=minute.calendar_version,
        raw_identifier=MADIS_OMO_TEMPERATURE_VARIABLE,
        possible_canonical_f=(mapped_f,),
        fail_closed_reason=None,
        metadata={
            **dict(minute.metadata),
            "source": MADIS_OMO_SOURCE,
            "omo_temperature_semantics": OMO_TEMPERATURE_SEMANTICS,
            "direct_omo_climate_state": True,
            "second_rolling_average_applied": False,
            "temperature_sensor_status": minute.temperature_sensor_status,
            "sensor_status_verified": minute.sensor_status_verified,
            "sequence_key": minute.sequence_key,
            "mapping_derivation_id": mapping.derivation_id,
            "mapping_model_version": MAPPING_MODEL_VERSION,
            "mapping_policy": policy.to_dict(),
            "benchmark_eligible": False,
        },
    )
    return _direct_result(
        minute,
        mapping,
        DirectOmoClimateStatus.RESEARCH_EVIDENCE,
        evidence,
        None,
        base_id_parts,
    )


def derive_direct_omo_batch(
    minutes: Iterable[MadisOmoMinute],
    policy: MadisKelvinEncodingPolicy | None,
) -> DirectOmoClimateBatch:
    """Decode a causal OMO collection without interpolation or re-averaging.

    Missing observation minutes simply produce no evidence. Exact duplicate
    inputs are idempotent. If different accepted values claim the same station,
    climate date, and physical observation minute, that minute fails closed as a
    conflict. Late/out-of-order arrival remains usable only at its actual Mercury
    known time because each evidence item preserves its own receipt clocks.
    """
    unique_minutes: dict[str, MadisOmoMinute] = {}
    for minute in minutes:
        unique_minutes.setdefault(minute.minute_id, minute)

    preliminary = [
        derive_direct_omo_climate_evidence(minute, map_madis_minute(minute, policy))
        for minute in unique_minutes.values()
    ]

    groups: dict[tuple[str, date, datetime], list[int]] = {}
    for index, result in enumerate(preliminary):
        if result.research_usable:
            groups.setdefault(
                (result.station_code, result.climate_date, result.observed_at),
                [],
            ).append(index)

    for indices in groups.values():
        values = {preliminary[index].mapped_f for index in indices}
        if len(values) <= 1:
            continue
        raw_ids = tuple(sorted(
            unique_minutes[preliminary[index].minute_id].raw_record_id
            for index in indices
        ))
        conflict_id = _stable_id(
            "madis-omo-minute-conflict",
            *raw_ids,
            DIRECT_OMO_EVIDENCE_MODEL_VERSION,
        )
        for index in indices:
            preliminary[index] = replace(
                preliminary[index],
                result_id=conflict_id,
                status=DirectOmoClimateStatus.CONFLICTING_SAME_MINUTE,
                evidence=None,
                fail_closed_reason="conflicting_accepted_omo_values_for_same_observation_minute",
                conflicting_raw_record_ids=raw_ids,
            )

    ordered = tuple(sorted(
        preliminary,
        key=lambda result: (
            result.mercury_known_at,
            result.observed_at,
            result.station_code,
            result.minute_id,
        ),
    ))
    evidence = tuple(
        result.evidence
        for result in ordered
        if result.research_usable and result.evidence is not None
    )
    return DirectOmoClimateBatch(results=ordered, evidence=evidence)


def _result(
    minute: MadisOmoMinute,
    observed: Decimal | None,
    candidates: tuple[int, ...],
    status: MinuteMappingStatus,
    policy: MadisKelvinEncodingPolicy | None,
    reason: str | None,
) -> CandidateMinuteF:
    derivation_id = _stable_id(
        "madis-omo-5m-f",
        minute.minute_id,
        format(observed, "f") if observed is not None else "missing",
        ",".join(str(v) for v in candidates),
        policy.policy_version if policy is not None else "no-policy",
        format(policy.resolution_k, "f") if policy is not None else "no-resolution",
        policy.rounding_rule.value if policy is not None else "no-rounding",
        MAPPING_MODEL_VERSION,
    )
    return CandidateMinuteF(
        derivation_id=derivation_id,
        minute_id=minute.minute_id,
        raw_record_id=minute.raw_record_id,
        station_code=minute.station_code,
        observed_kelvin=observed,
        candidates_f=candidates,
        status=status,
        policy=policy,
        fail_closed_reason=reason,
    )


def _direct_result(
    minute: MadisOmoMinute,
    mapping: CandidateMinuteF,
    status: DirectOmoClimateStatus,
    evidence: SettlementEvidence | None,
    reason: str | None,
    id_parts: tuple[Any, ...],
) -> DirectOmoClimateResult:
    return DirectOmoClimateResult(
        result_id=_stable_id("madis-omo-direct-result", status.value, *id_parts),
        minute_id=minute.minute_id,
        station_code=minute.station_code,
        climate_date=minute.climate_date,
        observed_at=minute.observed_at,
        mercury_known_at=minute.mercury_interpreted_at or minute.ldm_received_at,
        mapping_derivation_id=mapping.derivation_id,
        mapped_f=mapping.unique_f,
        status=status,
        evidence=evidence,
        fail_closed_reason=reason,
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}:{sha256(payload).hexdigest()[:24]}"
