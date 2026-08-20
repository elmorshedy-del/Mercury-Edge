from __future__ import annotations

"""Versioned information-visibility model for Mercury research and replay.

This module does not infer trader psychology. It answers the narrower, testable
question: what settlement-relevant information was available through Mercury's
ordinary-public, specialized-public, and validation channels at a given time?

The market journal remains the source of truth for what traders actually quoted
and traded. Joining that journal to these visibility events later allows the
backtester to estimate when the crowd reacted, what information it plausibly
had, and where Mercury possessed an information lead.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from typing import Any, Iterable, Mapping

from hard_information_domain import (
    EvidenceTrust,
    EvidenceType,
    IntegrityStatus,
    SettlementEvidence,
)

VISIBILITY_MODEL_VERSION = "information-visibility-v1"
PUBLIC_SOURCE_POLICY_VERSION = "ordinary-public-v1-noaa-awc"


class VisibilityClass(str, Enum):
    """Audience/channel class, not a claim that any specific trader saw it."""

    ORDINARY_PUBLIC = "ordinary_public"
    SPECIALIZED_PUBLIC = "specialized_public"
    VALIDATION_ONLY = "validation_only"
    UNKNOWN = "unknown"


class AvailabilityBasis(str, Enum):
    """Why the disclosure timestamp is safe to use in causal replay."""

    FIRST_FETCHABLE = "first_fetchable"
    SOURCE_PUBLISHED = "source_published"
    MERCURY_PUBLIC_FETCH = "mercury_public_fetch"
    MERCURY_RECEIPT = "mercury_receipt"


_BAD_INTEGRITY = {
    IntegrityStatus.OFF_LATTICE,
    IntegrityStatus.CONFLICT,
    IntegrityStatus.INCOMPLETE,
    IntegrityStatus.INVALID_WINDOW,
    IntegrityStatus.UNKNOWN,
}
_CURRENT_TYPES = {
    EvidenceType.ASOS_MAIN_CURRENT,
    EvidenceType.ASOS_T_GROUP_CURRENT,
}
_ASOS_PUBLIC_TYPES = {
    EvidenceType.ASOS_MAIN_CURRENT,
    EvidenceType.ASOS_T_GROUP_CURRENT,
    EvidenceType.ASOS_SIX_HOUR_MAX,
    EvidenceType.ASOS_24H_MAX,
}
_MADIS_TYPES = {
    EvidenceType.MADIS_OMO_1MIN,
    EvidenceType.MADIS_RECONSTRUCTED_5M,
}
_VALIDATION_TYPES = {
    EvidenceType.DSM_MAX,
    EvidenceType.CLI_MAX,
    EvidenceType.KALSHI_SETTLEMENT,
}
_CURRENT_PRIORITY = {
    EvidenceType.ASOS_MAIN_CURRENT: 1,
    EvidenceType.ASOS_T_GROUP_CURRENT: 2,
}


@dataclass(frozen=True)
class PublicDisclosure:
    """One versioned fact exposed by a source record and its causal availability.

    Despite the historical name, `visibility` may be specialized-public or
    validation-only. The class represents an observable disclosure event; the
    visibility label says which configured audience/channel may use it.
    """

    disclosure_id: str
    evidence_id: str
    evidence_type: EvidenceType
    source: str
    product: str
    station_code: str
    climate_date: date
    visibility: VisibilityClass
    available_at: datetime
    availability_basis: AvailabilityBasis
    observed_at: datetime
    source_published_at: datetime | None
    first_fetchable_at: datetime | None
    mercury_first_seen_at: datetime
    mercury_interpreted_at: datetime | None
    proven_min_f: int | None
    proven_max_f: int | None
    possible_canonical_f: tuple[int, ...]
    raw_identifier: str | None
    source_record_ids: tuple[str, ...]
    source_payload_hash: str | None
    parser_version: str
    evidence_model_version: str
    calendar_version: str
    integrity_status: IntegrityStatus
    trust: EvidenceTrust
    visibility_model_version: str = VISIBILITY_MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)


# Backward-compatible alias for the first implementation name.
InformationDisclosure = PublicDisclosure


@dataclass(frozen=True)
class PublicInformationState:
    """Ordinary-public weather state as known by a causal replay timestamp.

    `public_daily_high_min_f` is monotonic evidence about the day's maximum.
    Current-temperature fields instead describe the newest public observations,
    which may rise or fall during the day. Main and precise T-group facts remain
    separately inspectable so later crowd-reaction analysis does not lose what
    the ordinary report actually displayed.
    """

    state_id: str
    station_code: str
    climate_date: date
    as_of: datetime
    public_daily_high_min_f: int | None

    latest_main_current_proven_min_f: int | None
    latest_main_current_possible_f: tuple[int, ...]
    latest_main_current_observed_at: datetime | None
    latest_main_current_disclosure_id: str | None

    latest_precise_current_proven_min_f: int | None
    latest_precise_current_possible_f: tuple[int, ...]
    latest_precise_current_observed_at: datetime | None
    latest_precise_current_disclosure_id: str | None

    latest_current_proven_min_f: int | None
    latest_current_possible_f: tuple[int, ...]
    latest_current_evidence_type: EvidenceType | None
    latest_current_observed_at: datetime | None
    latest_current_disclosure_id: str | None

    latest_six_hour_max_min_f: int | None
    latest_six_hour_observed_at: datetime | None
    latest_six_hour_disclosure_id: str | None

    last_public_disclosure_at: datetime | None
    supporting_evidence_ids: tuple[str, ...]
    supporting_disclosure_ids: tuple[str, ...]
    public_source_policy_version: str = PUBLIC_SOURCE_POLICY_VERSION
    visibility_model_version: str = VISIBILITY_MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)


@dataclass(frozen=True)
class InformationView:
    """Synchronized information state; market prices are joined downstream."""

    station_code: str
    climate_date: date
    as_of: datetime
    ordinary_public: PublicInformationState
    mercury_research_high_min_f: int | None
    mercury_benchmark_high_min_f: int | None
    specialized_public_high_min_f: int | None
    research_vs_public_gap_f: int | None
    benchmark_vs_public_gap_f: int | None
    visibility_model_version: str = VISIBILITY_MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)


def classify_visibility(evidence: SettlementEvidence) -> VisibilityClass:
    """Classify the channel conservatively from type plus source provenance."""
    if evidence.evidence_type in _VALIDATION_TYPES:
        return VisibilityClass.VALIDATION_ONLY
    if evidence.evidence_type in _MADIS_TYPES:
        return VisibilityClass.SPECIALIZED_PUBLIC
    if evidence.evidence_type in _ASOS_PUBLIC_TYPES:
        source = str(evidence.metadata.get("source") or "").upper().strip()
        # The currently supported ordinary-public weather path is NOAA/AWC.
        # Unknown provenance fails closed rather than being labelled crowd-visible.
        if source == "NOAA_AWC":
            return VisibilityClass.ORDINARY_PUBLIC
    return VisibilityClass.UNKNOWN


def disclosure_time(evidence: SettlementEvidence) -> tuple[datetime, AvailabilityBasis]:
    """Return the earliest defensible availability time; never use observation time.

    When exact first-fetchability/publication is unavailable, an ordinary-public
    product uses Mercury's first successful public fetch as a conservative upper
    bound on availability. Specialized feeds fall back to Mercury receipt time.
    """
    clocks = evidence.clocks
    if clocks.first_fetchable_at is not None:
        return clocks.first_fetchable_at, AvailabilityBasis.FIRST_FETCHABLE
    if clocks.source_published_at is not None:
        return clocks.source_published_at, AvailabilityBasis.SOURCE_PUBLISHED
    if classify_visibility(evidence) is VisibilityClass.ORDINARY_PUBLIC:
        return clocks.mercury_received_at, AvailabilityBasis.MERCURY_PUBLIC_FETCH
    return clocks.mercury_received_at, AvailabilityBasis.MERCURY_RECEIPT


def to_disclosure(evidence: SettlementEvidence) -> PublicDisclosure:
    available_at, basis = disclosure_time(evidence)
    visibility = classify_visibility(evidence)
    source = str(evidence.metadata.get("source") or "unknown")
    product = str(
        evidence.metadata.get("report_type")
        or evidence.metadata.get("upstream_variable")
        or evidence.evidence_type.value
    )
    raw_hash = evidence.metadata.get("raw_payload_hash")
    source_payload_hash = str(raw_hash) if raw_hash else None
    disclosure_id = _stable_id(
        "public-disclosure",
        evidence.evidence_id,
        visibility.value,
        available_at.isoformat(),
        basis.value,
        VISIBILITY_MODEL_VERSION,
    )
    return PublicDisclosure(
        disclosure_id=disclosure_id,
        evidence_id=evidence.evidence_id,
        evidence_type=evidence.evidence_type,
        source=source,
        product=product,
        station_code=evidence.station_code,
        climate_date=evidence.climate_date,
        visibility=visibility,
        available_at=available_at,
        availability_basis=basis,
        observed_at=evidence.clocks.observed_at,
        source_published_at=evidence.clocks.source_published_at,
        first_fetchable_at=evidence.clocks.first_fetchable_at,
        mercury_first_seen_at=evidence.clocks.mercury_received_at,
        mercury_interpreted_at=evidence.clocks.mercury_interpreted_at,
        proven_min_f=evidence.proven_min_f,
        proven_max_f=evidence.proven_max_f,
        possible_canonical_f=evidence.possible_canonical_f,
        raw_identifier=evidence.raw_identifier,
        source_record_ids=evidence.source_record_ids,
        source_payload_hash=source_payload_hash,
        parser_version=evidence.parser_version,
        evidence_model_version=evidence.evidence_model_version,
        calendar_version=evidence.calendar_version,
        integrity_status=evidence.integrity_status,
        trust=evidence.trust,
    )


def disclosures_for_evidence(
    evidence_items: Iterable[SettlementEvidence],
    *,
    station_code: str | None = None,
    climate_date: date | None = None,
    as_of: datetime | None = None,
) -> tuple[PublicDisclosure, ...]:
    """Deterministically derive disclosure objects from canonical evidence."""
    items: list[PublicDisclosure] = []
    for evidence in evidence_items:
        if station_code is not None and evidence.station_code != station_code:
            continue
        if climate_date is not None and evidence.climate_date != climate_date:
            continue
        disclosure = to_disclosure(evidence)
        if as_of is not None and disclosure.available_at > as_of:
            continue
        items.append(disclosure)
    return tuple(sorted(
        items,
        key=lambda item: (
            item.available_at,
            item.observed_at,
            item.evidence_type.value,
            item.disclosure_id,
        ),
    ))


def build_public_information_state(
    evidence_items: Iterable[SettlementEvidence],
    *,
    station_code: str,
    climate_date: date,
    as_of: datetime,
) -> PublicInformationState:
    """Build ordinary-public state without inferring market belief."""
    disclosures = [
        item
        for item in disclosures_for_evidence(
            evidence_items,
            station_code=station_code,
            climate_date=climate_date,
            as_of=as_of,
        )
        if item.visibility is VisibilityClass.ORDINARY_PUBLIC
    ]
    usable = [
        item
        for item in disclosures
        if item.integrity_status not in _BAD_INTEGRITY
    ]

    bound_items = [item for item in usable if item.proven_min_f is not None]
    public_bound = max((item.proven_min_f for item in bound_items), default=None)
    bound_support = tuple(sorted(
        (
            (item.evidence_id, item.disclosure_id)
            for item in bound_items
            if public_bound is not None and item.proven_min_f == public_bound
        ),
        key=lambda pair: pair[1],
    ))

    main_items = [
        item for item in usable
        if item.evidence_type is EvidenceType.ASOS_MAIN_CURRENT
    ]
    precise_items = [
        item for item in usable
        if item.evidence_type is EvidenceType.ASOS_T_GROUP_CURRENT
    ]
    current_items = [item for item in usable if item.evidence_type in _CURRENT_TYPES]

    latest_main = _latest_current(main_items)
    latest_precise = _latest_current(precise_items)
    latest_current = max(
        current_items,
        key=lambda item: (
            item.observed_at,
            _CURRENT_PRIORITY.get(item.evidence_type, 0),
            item.available_at,
            item.evidence_id,
        ),
        default=None,
    )

    six_hour_items = [
        item for item in usable
        if item.evidence_type is EvidenceType.ASOS_SIX_HOUR_MAX
    ]
    latest_six = max(
        six_hour_items,
        key=lambda item: (item.observed_at, item.available_at, item.evidence_id),
        default=None,
    )
    last_public = max((item.available_at for item in usable), default=None)

    state_id = _stable_id(
        "public-information-state",
        station_code,
        climate_date.isoformat(),
        as_of.isoformat(),
        public_bound,
        latest_main.disclosure_id if latest_main else "none",
        latest_precise.disclosure_id if latest_precise else "none",
        latest_six.disclosure_id if latest_six else "none",
        PUBLIC_SOURCE_POLICY_VERSION,
        VISIBILITY_MODEL_VERSION,
    )
    return PublicInformationState(
        state_id=state_id,
        station_code=station_code,
        climate_date=climate_date,
        as_of=as_of,
        public_daily_high_min_f=public_bound,
        latest_main_current_proven_min_f=latest_main.proven_min_f if latest_main else None,
        latest_main_current_possible_f=latest_main.possible_canonical_f if latest_main else (),
        latest_main_current_observed_at=latest_main.observed_at if latest_main else None,
        latest_main_current_disclosure_id=latest_main.disclosure_id if latest_main else None,
        latest_precise_current_proven_min_f=latest_precise.proven_min_f if latest_precise else None,
        latest_precise_current_possible_f=latest_precise.possible_canonical_f if latest_precise else (),
        latest_precise_current_observed_at=latest_precise.observed_at if latest_precise else None,
        latest_precise_current_disclosure_id=latest_precise.disclosure_id if latest_precise else None,
        latest_current_proven_min_f=latest_current.proven_min_f if latest_current else None,
        latest_current_possible_f=latest_current.possible_canonical_f if latest_current else (),
        latest_current_evidence_type=latest_current.evidence_type if latest_current else None,
        latest_current_observed_at=latest_current.observed_at if latest_current else None,
        latest_current_disclosure_id=latest_current.disclosure_id if latest_current else None,
        latest_six_hour_max_min_f=latest_six.proven_min_f if latest_six else None,
        latest_six_hour_observed_at=latest_six.observed_at if latest_six else None,
        latest_six_hour_disclosure_id=latest_six.disclosure_id if latest_six else None,
        last_public_disclosure_at=last_public,
        supporting_evidence_ids=tuple(pair[0] for pair in bound_support),
        supporting_disclosure_ids=tuple(pair[1] for pair in bound_support),
    )


def build_information_view(
    evidence_items: Iterable[SettlementEvidence],
    *,
    station_code: str,
    climate_date: date,
    as_of: datetime,
) -> InformationView:
    """Build Mercury/public information states at one causal timestamp."""
    items = tuple(
        item for item in evidence_items
        if item.station_code == station_code and item.climate_date == climate_date
    )
    public_state = build_public_information_state(
        items,
        station_code=station_code,
        climate_date=climate_date,
        as_of=as_of,
    )

    known = [
        item
        for item in items
        if _mercury_known_at(item) <= as_of
        and item.integrity_status not in _BAD_INTEGRITY
        and item.proven_min_f is not None
    ]
    research_bound = max(
        (
            item.proven_min_f
            for item in known
            if item.trust in {EvidenceTrust.BENCHMARK_ELIGIBLE, EvidenceTrust.RESEARCH_ONLY}
        ),
        default=None,
    )
    benchmark_bound = max(
        (item.proven_min_f for item in known if item.benchmark_eligible),
        default=None,
    )
    specialized_bound = max(
        (
            item.proven_min_f
            for item in known
            if classify_visibility(item) is VisibilityClass.SPECIALIZED_PUBLIC
            and item.trust in {EvidenceTrust.BENCHMARK_ELIGIBLE, EvidenceTrust.RESEARCH_ONLY}
        ),
        default=None,
    )
    public_bound = public_state.public_daily_high_min_f
    return InformationView(
        station_code=station_code,
        climate_date=climate_date,
        as_of=as_of,
        ordinary_public=public_state,
        mercury_research_high_min_f=research_bound,
        mercury_benchmark_high_min_f=benchmark_bound,
        specialized_public_high_min_f=specialized_bound,
        research_vs_public_gap_f=_gap(research_bound, public_bound),
        benchmark_vs_public_gap_f=_gap(benchmark_bound, public_bound),
    )


def first_ordinary_public_time_proving_bound(
    evidence_items: Iterable[SettlementEvidence],
    *,
    station_code: str,
    climate_date: date,
    target_bound_f: int,
) -> datetime | None:
    """Earliest defensible time ordinary-public evidence caught up to a bound."""
    candidates: list[datetime] = []
    for evidence in evidence_items:
        if evidence.station_code != station_code or evidence.climate_date != climate_date:
            continue
        if classify_visibility(evidence) is not VisibilityClass.ORDINARY_PUBLIC:
            continue
        if evidence.integrity_status in _BAD_INTEGRITY or evidence.proven_min_f is None:
            continue
        if evidence.proven_min_f < int(target_bound_f):
            continue
        candidates.append(disclosure_time(evidence)[0])
    return min(candidates, default=None)


def _latest_current(items: list[PublicDisclosure]) -> PublicDisclosure | None:
    return max(
        items,
        key=lambda item: (item.observed_at, item.available_at, item.evidence_id),
        default=None,
    )


def _mercury_known_at(evidence: SettlementEvidence) -> datetime:
    return evidence.clocks.mercury_interpreted_at or evidence.clocks.mercury_received_at


def _gap(mercury_bound: int | None, public_bound: int | None) -> int | None:
    if mercury_bound is None or public_bound is None:
        return None
    return mercury_bound - public_bound


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}:{sha256(payload).hexdigest()[:24]}"


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
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
