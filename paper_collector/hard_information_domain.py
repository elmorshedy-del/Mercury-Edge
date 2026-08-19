from __future__ import annotations

"""Canonical domain contracts for Mercury hard-information trading.

These types deliberately separate source transport, normalized observations,
settlement evidence, derived climate state, bucket elimination, execution state,
and post-trade settlement truth.

The module contains no source-specific parser and no strategy logic.  That is an
architectural invariant: weather syntax belongs in adapters/parsers; strategies
consume only canonical hard state and elimination objects.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

DOMAIN_SCHEMA_VERSION = "hard-information-domain-v1"


class IntegrityStatus(str, Enum):
    CANONICAL = "canonical"
    AMBIGUOUS = "ambiguous"
    OFF_LATTICE = "off_lattice"
    CONFLICT = "conflict"
    INCOMPLETE = "incomplete"
    INVALID_WINDOW = "invalid_window"
    UNKNOWN = "unknown"


class EvidenceTrust(str, Enum):
    """Whether an evidence item may change benchmark hard state."""

    BENCHMARK_ELIGIBLE = "benchmark_eligible"
    VALIDATION_ONLY = "validation_only"
    RESEARCH_ONLY = "research_only"
    REJECTED = "rejected"


class EvidenceType(str, Enum):
    ASOS_MAIN_CURRENT = "asos_main_current"
    ASOS_T_GROUP_CURRENT = "asos_t_group_current"
    ASOS_SIX_HOUR_MAX = "asos_six_hour_max"
    ASOS_24H_MAX = "asos_24h_max"
    MADIS_OMO_1MIN = "madis_omo_1min"
    MADIS_RECONSTRUCTED_5M = "madis_reconstructed_5m"
    DSM_MAX = "dsm_max"
    CLI_MAX = "cli_max"
    KALSHI_SETTLEMENT = "kalshi_settlement"


@dataclass(frozen=True)
class SourceClocks:
    """Distinct clocks are never collapsed into one generic timestamp."""

    observed_at: datetime
    mercury_received_at: datetime
    mercury_interpreted_at: datetime | None = None
    source_published_at: datetime | None = None
    first_fetchable_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceClocks":
        return cls(
            observed_at=_datetime(value["observed_at"]),
            mercury_received_at=_datetime(value["mercury_received_at"]),
            mercury_interpreted_at=_optional_datetime(value.get("mercury_interpreted_at")),
            source_published_at=_optional_datetime(value.get("source_published_at")),
            first_fetchable_at=_optional_datetime(value.get("first_fetchable_at")),
        )


@dataclass(frozen=True)
class RawSourceRecord:
    """Reference to an immutable raw-journal item.

    `payload_hash` identifies the exact bytes/text preserved by the raw journal.
    The payload itself intentionally does not live in this domain object.
    """

    record_id: str
    source: str
    station_code: str | None
    payload_hash: str
    clocks: SourceClocks
    transport: str | None = None
    sequence_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RawSourceRecord":
        return cls(
            record_id=str(value["record_id"]),
            source=str(value["source"]),
            station_code=_optional_str(value.get("station_code")),
            payload_hash=str(value["payload_hash"]),
            clocks=SourceClocks.from_dict(_mapping(value["clocks"])),
            transport=_optional_str(value.get("transport")),
            sequence_key=_optional_str(value.get("sequence_key")),
        )


@dataclass(frozen=True)
class NormalizedObservation:
    """Source-neutral observation produced by a parser/adapter."""

    observation_id: str
    source_record_id: str
    station_code: str
    climate_date: date
    observation_type: str
    value: Decimal | int | str
    unit: str | None
    clocks: SourceClocks
    parser_version: str
    calendar_version: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)


@dataclass(frozen=True)
class SettlementEvidence:
    """A settlement-oriented claim derived from one or more observations."""

    evidence_id: str
    evidence_type: EvidenceType
    station_code: str
    climate_date: date
    source_record_ids: tuple[str, ...]
    proven_min_f: int | None
    proven_max_f: int | None
    integrity_status: IntegrityStatus
    trust: EvidenceTrust
    clocks: SourceClocks
    parser_version: str
    evidence_model_version: str
    calendar_version: str
    raw_identifier: str | None = None
    possible_canonical_f: tuple[int, ...] = ()
    fail_closed_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def benchmark_eligible(self) -> bool:
        return (
            self.trust is EvidenceTrust.BENCHMARK_ELIGIBLE
            and self.integrity_status not in {
                IntegrityStatus.OFF_LATTICE,
                IntegrityStatus.CONFLICT,
                IntegrityStatus.INCOMPLETE,
                IntegrityStatus.INVALID_WINDOW,
                IntegrityStatus.UNKNOWN,
            }
            and self.proven_min_f is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)


@dataclass(frozen=True)
class HardClimateState:
    """Source-agnostic monotonic lower bound for one station climate day."""

    state_id: str
    station_code: str
    climate_date: date
    proven_daily_high_min_f: int
    first_known_at: datetime
    transition_evidence_id: str
    supporting_evidence_ids: tuple[str, ...]
    state_model_version: str
    calendar_version: str

    def proves_above(self, upper_bound_f: int | Decimal) -> bool:
        return Decimal(self.proven_daily_high_min_f) > Decimal(str(upper_bound_f))

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HardClimateState":
        return cls(
            state_id=str(value["state_id"]),
            station_code=str(value["station_code"]),
            climate_date=_date(value["climate_date"]),
            proven_daily_high_min_f=int(value["proven_daily_high_min_f"]),
            first_known_at=_datetime(value["first_known_at"]),
            transition_evidence_id=str(value["transition_evidence_id"]),
            supporting_evidence_ids=tuple(str(v) for v in value.get("supporting_evidence_ids", [])),
            state_model_version=str(value["state_model_version"]),
            calendar_version=str(value["calendar_version"]),
        )


@dataclass(frozen=True)
class BucketElimination:
    """Pure mathematical consequence of a HardClimateState and exact strike rule."""

    elimination_id: str
    event_ticker: str
    market_ticker: str
    station_code: str
    climate_date: date
    hard_state_id: str
    hard_lower_bound_f: int
    strike_rule: str
    eliminated: bool
    elimination_model_version: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)


@dataclass(frozen=True)
class ExecutableMarketState:
    """Execution facts only; no weather interpretation belongs here."""

    event_ticker: str
    market_ticker: str
    captured_at: datetime
    no_ask: Decimal | None
    no_ask_quantity: Decimal | None
    fee_estimate: Decimal | None
    market_data_version: str
    source_record_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)


@dataclass(frozen=True)
class SettlementTruth:
    """Validation/settlement label.  It is not ordinary intraday trade evidence."""

    truth_id: str
    source: str
    station_code: str
    climate_date: date
    final_max_f: int | None
    status: str
    source_record_id: str
    observed_or_issued_at: datetime
    truth_model_version: str
    revision_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)


# ---------------------------------------------------------------------------
# Deterministic serialization helpers.
# ---------------------------------------------------------------------------


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return {name: _encode(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _encode(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if isinstance(value, list):
        return [_encode(item) for item in value]
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"expected mapping, got {type(value)!r}")
    return value


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _optional_datetime(value: Any) -> datetime | None:
    return None if value is None else _datetime(value)


def _date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
