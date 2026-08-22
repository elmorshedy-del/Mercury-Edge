from __future__ import annotations

"""Source-agnostic monotonic hard-state accumulation.

This module is deliberately ignorant of METAR, MADIS, Celsius, ASOS group
syntax, and Kalshi strikes. It consumes only canonical SettlementEvidence.
That makes a future MADIS reconstruction or another settlement-compatible
source a local adapter change rather than a strategy rewrite.

Knowledge arriving at exactly the same Mercury-usable timestamp is treated as
one atomic batch. That matters for a routine METAR containing both a lower
current temperature and a stronger six-hour maximum: Mercury learns both facts
simultaneously, so the state jumps directly to the strongest bound rather than
inventing intermediate tradeable transitions inside one network receipt.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from itertools import groupby
from typing import Any, Iterable

from hard_information_domain import HardClimateState, SettlementEvidence
from market_calendar import CLIMATE_CALENDAR_VERSION

HARD_STATE_ACCUMULATOR_VERSION = "hard-state-accumulator-v1"


class ApplicationStatus(str, Enum):
    TRANSITION = "transition"
    CORROBORATION = "corroboration"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class EvidenceApplication:
    evidence_id: str
    status: ApplicationStatus
    reason: str
    known_at: datetime
    proven_min_f: int | None
    prior_bound_f: int | None
    resulting_bound_f: int | None
    evidence_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "status": self.status.value,
            "reason": self.reason,
            "known_at": self.known_at.isoformat(),
            "proven_min_f": self.proven_min_f,
            "prior_bound_f": self.prior_bound_f,
            "resulting_bound_f": self.resulting_bound_f,
            "evidence_type": self.evidence_type,
        }


@dataclass(frozen=True)
class HardStateTimeline:
    station_code: str
    climate_date: date
    calendar_version: str
    applications: tuple[EvidenceApplication, ...]
    states: tuple[HardClimateState, ...]
    accumulator_version: str = HARD_STATE_ACCUMULATOR_VERSION

    @property
    def current_state(self) -> HardClimateState | None:
        return self.states[-1] if self.states else None

    @property
    def current_bound_f(self) -> int | None:
        state = self.current_state
        return None if state is None else state.proven_daily_high_min_f

    @property
    def transition_evidence_ids(self) -> tuple[str, ...]:
        return tuple(state.transition_evidence_id for state in self.states)

    def is_transition_evidence(self, evidence_id: str) -> bool:
        return evidence_id in self.transition_evidence_ids

    def application_for(self, evidence_id: str) -> EvidenceApplication | None:
        return next((item for item in self.applications if item.evidence_id == evidence_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "station_code": self.station_code,
            "climate_date": self.climate_date.isoformat(),
            "calendar_version": self.calendar_version,
            "accumulator_version": self.accumulator_version,
            "applications": [item.to_dict() for item in self.applications],
            "states": [state.to_dict() for state in self.states],
            "current_bound_f": self.current_bound_f,
        }


def evidence_known_at(evidence: SettlementEvidence) -> datetime:
    """Earliest timestamp at which Mercury had an interpreted usable fact.

    `first_fetchable_at` and `source_published_at` remain distinct latency-study
    clocks; they do not authorize a live decision before Mercury actually
    received/interpreted the record. Observation time never substitutes for a
    receipt clock, preventing replay look-ahead.
    """
    return evidence.clocks.mercury_interpreted_at or evidence.clocks.mercury_received_at


def accumulate_hard_state(
    evidence_items: Iterable[SettlementEvidence],
    *,
    station_code: str,
    climate_date: date,
    calendar_version: str = CLIMATE_CALENDAR_VERSION,
) -> HardStateTimeline:
    """Apply canonical evidence in causal Mercury-knowledge order.

    Only explicitly benchmark-eligible evidence for the exact station, climate
    day, and requested calendar version can change the lower bound. State is
    monotonic. Equal/lower later facts are retained as corroborations. Rejected
    evidence is also retained with an explicit reason rather than disappearing.

    All evidence with the same `known_at` timestamp is an atomic knowledge
    batch. At most one transition may be created by a batch, using the strongest
    proven lower bound and a deterministic evidence-id tie-break.
    """
    ordered = sorted(
        list(evidence_items),
        key=lambda item: (
            evidence_known_at(item),
            item.clocks.observed_at,
            item.evidence_id,
        ),
    )

    applications: list[EvidenceApplication] = []
    states: list[HardClimateState] = []
    seen_ids: set[str] = set()
    current_bound: int | None = None

    for known_at, batch_iter in groupby(ordered, key=evidence_known_at):
        batch = list(batch_iter)
        prior_bound = current_bound
        accepted: list[SettlementEvidence] = []

        for evidence in batch:
            if evidence.evidence_id in seen_ids:
                applications.append(_application(
                    evidence,
                    ApplicationStatus.DUPLICATE,
                    "duplicate_evidence_id",
                    known_at,
                    prior_bound,
                    current_bound,
                ))
                continue
            seen_ids.add(evidence.evidence_id)

            rejection = _rejection_reason(
                evidence,
                station_code=station_code,
                climate_date=climate_date,
                calendar_version=calendar_version,
            )
            if rejection is not None:
                applications.append(_application(
                    evidence,
                    ApplicationStatus.REJECTED,
                    rejection,
                    known_at,
                    prior_bound,
                    current_bound,
                ))
                continue
            accepted.append(evidence)

        if not accepted:
            continue

        strongest_bound = max(int(item.proven_min_f) for item in accepted if item.proven_min_f is not None)
        strongest = sorted(
            (item for item in accepted if int(item.proven_min_f) == strongest_bound),
            key=lambda item: item.evidence_id,
        )
        transition_evidence = strongest[0]
        batch_raises = current_bound is None or strongest_bound > current_bound

        if batch_raises:
            current_bound = strongest_bound
            state = HardClimateState(
                state_id=_state_id(
                    station_code,
                    climate_date,
                    strongest_bound,
                    transition_evidence.evidence_id,
                    calendar_version,
                ),
                station_code=station_code,
                climate_date=climate_date,
                proven_daily_high_min_f=strongest_bound,
                first_known_at=known_at,
                transition_evidence_id=transition_evidence.evidence_id,
                supporting_evidence_ids=tuple(sorted(item.evidence_id for item in strongest)),
                state_model_version=HARD_STATE_ACCUMULATOR_VERSION,
                calendar_version=calendar_version,
            )
            states.append(state)

        for evidence in accepted:
            candidate_bound = int(evidence.proven_min_f)
            if batch_raises and evidence.evidence_id == transition_evidence.evidence_id:
                status = ApplicationStatus.TRANSITION
                reason = "initial_bound" if prior_bound is None else "raised_bound"
            elif batch_raises and candidate_bound == strongest_bound:
                status = ApplicationStatus.CORROBORATION
                reason = "same_batch_equal_bound"
            elif batch_raises:
                status = ApplicationStatus.CORROBORATION
                reason = "same_batch_lower_bound"
            else:
                status = ApplicationStatus.CORROBORATION
                reason = "equal_bound" if candidate_bound == current_bound else "lower_bound_preserved"
            applications.append(_application(
                evidence,
                status,
                reason,
                known_at,
                prior_bound,
                current_bound,
            ))

    return HardStateTimeline(
        station_code=station_code,
        climate_date=climate_date,
        calendar_version=calendar_version,
        applications=tuple(applications),
        states=tuple(states),
    )


def _application(
    evidence: SettlementEvidence,
    status: ApplicationStatus,
    reason: str,
    known_at: datetime,
    prior_bound: int | None,
    resulting_bound: int | None,
) -> EvidenceApplication:
    return EvidenceApplication(
        evidence_id=evidence.evidence_id,
        status=status,
        reason=reason,
        known_at=known_at,
        proven_min_f=evidence.proven_min_f,
        prior_bound_f=prior_bound,
        resulting_bound_f=resulting_bound,
        evidence_type=evidence.evidence_type.value,
    )


def _rejection_reason(
    evidence: SettlementEvidence,
    *,
    station_code: str,
    climate_date: date,
    calendar_version: str,
) -> str | None:
    if evidence.station_code != station_code:
        return "station_mismatch"
    if evidence.climate_date != climate_date:
        return "climate_date_mismatch"
    if evidence.calendar_version != calendar_version:
        return "calendar_version_mismatch"
    if not evidence.benchmark_eligible:
        return "not_benchmark_eligible"
    if evidence.proven_min_f is None:
        return "missing_proven_min_f"
    return None


def _state_id(
    station_code: str,
    climate_date: date,
    bound_f: int,
    transition_evidence_id: str,
    calendar_version: str,
) -> str:
    raw = "|".join((
        HARD_STATE_ACCUMULATOR_VERSION,
        calendar_version,
        station_code,
        climate_date.isoformat(),
        str(bound_f),
        transition_evidence_id,
    )).encode("utf-8")
    return f"hard-state:{sha256(raw).hexdigest()[:24]}"
