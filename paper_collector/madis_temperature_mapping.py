from __future__ import annotations

"""Research-only inverse mapping from MADIS Kelvin to candidate ASOS minute °F.

The official MADIS OMO variable contract tells us that temperature ``T`` is
reported in Kelvin. It does not, by itself, prove the exact quantization and
rounding representation Mercury will receive through the eventual live LDM
path. Guessing that representation and doing continuous K->F->round would be
unsafe.

This module therefore requires an explicit, versioned source-encoding policy.
The policy forward-encodes each candidate integer Fahrenheit minute state to a
Kelvin wire value and builds the exact inverse candidate set for an observed
Kelvin value. Unknown policy, off-policy values, and ambiguous inverse sets all
fail closed for reconstruction.

Nothing here is benchmark evidence. It is a replaceable research derivation
that will be calibrated against real MADIS captures before any trust promotion.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
from enum import Enum
from hashlib import sha256
from typing import Any

from madis_omo import (
    MADIS_OMO_TEMPERATURE_UNIT,
    MADIS_OMO_TEMPERATURE_VARIABLE,
    MadisOmoMinute,
)

MAPPING_MODEL_VERSION = "madis-kelvin-inverse-lattice-v1"


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


@dataclass(frozen=True)
class MadisKelvinEncodingPolicy:
    """Explicit hypothesis for the live MADIS Kelvin representation.

    `resolution_k` is a true quantization increment, not merely decimal places.
    Example: 0.01 means the forward wire model rounds to the nearest 0.01 K.
    The policy is intentionally data, so later empirical calibration can swap
    it without changing reconstruction or strategy code.
    """

    resolution_k: Decimal
    rounding_rule: SourceRoundingRule
    min_f: int = -150
    max_f: int = 150
    policy_version: str = "unverified-madis-kelvin-policy-v1"

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
        }


@dataclass(frozen=True)
class CandidateMinuteF:
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
    def reconstruction_usable(self) -> bool:
        # Still research-only. This means only "usable as input to the Step 4G-B
        # research reconstruction", never benchmark/trade eligible.
        return self.unique_f is not None

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
            "benchmark_eligible": False,
        }


def fahrenheit_to_kelvin_exact(value_f: int) -> Decimal:
    """High-precision physical conversion before source-wire quantization."""
    with localcontext() as ctx:
        ctx.prec = 50
        return (Decimal(value_f) - Decimal(32)) * Decimal(5) / Decimal(9) + Decimal("273.15")


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
    return quantize_increment(
        fahrenheit_to_kelvin_exact(value_f),
        policy.resolution_k,
        policy.rounding_rule,
    )


def inverse_candidates_f(
    observed_kelvin: Decimal,
    policy: MadisKelvinEncodingPolicy,
) -> tuple[int, ...]:
    """Return every whole-°F minute state that encodes to this exact wire value."""
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
    """Map one accepted raw MADIS minute to an explicit inverse candidate set."""
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
            "madis_minute_not_research_usable",
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
            "no_integer_fahrenheit_state_matches_configured_kelvin_encoding",
        )
    if len(candidates) > 1:
        return _result(
            minute,
            observed,
            candidates,
            MinuteMappingStatus.AMBIGUOUS,
            policy,
            "configured_kelvin_encoding_maps_to_multiple_integer_fahrenheit_states",
        )
    return _result(
        minute,
        observed,
        candidates,
        MinuteMappingStatus.UNIQUE_RESEARCH,
        policy,
        None,
    )


def _result(
    minute: MadisOmoMinute,
    observed: Decimal | None,
    candidates: tuple[int, ...],
    status: MinuteMappingStatus,
    policy: MadisKelvinEncodingPolicy | None,
    reason: str | None,
) -> CandidateMinuteF:
    derivation_id = _stable_id(
        "madis-minute-f",
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


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}:{sha256(payload).hexdigest()[:24]}"
