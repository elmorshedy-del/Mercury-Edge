from __future__ import annotations

"""Source-agnostic monotonic hard-state accumulation.

This module is deliberately ignorant of METAR, MADIS, Celsius, ASOS group
syntax, and Kalshi strikes. It consumes only canonical SettlementEvidence.
That makes a future MADIS reconstruction or another settlement-compatible
source a local adapter change rather than a strategy rewrite.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "station_code": self.station_code,
            "climate_date": self.climate_date.isoformat(),
            "accumulator_version": self.accumulator_version,
            "applications": [item.to_dict() for item in self.applications],
            "states": [state.to_dict() for state in self.states],
            "current_bound_f": self.current_bound_f,
        }


def evidence_known_at(evidence: SettlementEvidence) -> datetime:
    """Earliest time Mercury can claim the interpreted fact was knowable.

    When a parser records an explicit interpretation-complete timestamp, use it.
    Legacy/current AWC rows only preserve network receipt, so receipt time is the
    conservative available fallback. Observation time never substitutes for
    receipt time and therefore cannot create look-ahead.
    """
    return evidence.clocks.mercury_interpreted_at or evidence.clocks.mercury_received_at


def accumulate_hard_state(
    evidence_items: Iterable[SettlementEvidence],
    *,
    station_code: str,
    climate_date: date,
) -> HardStateTimeline:
    """Apply canonical evidence in Mercury-knowledge order.

    Only explicitly benchmark-eligible evidence for the exact station and
    climate date can change the lower bound. Lower/equal later evidence is kept
    as corroboration but can never reduce state. Rejected evidence is retained
    in the timeline with an explicit reason instead of disappearing.
    """
    items = list(evidence_items)
    ordered = sorted(
        items,
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

    for evidence in ordered:
        known_at = evidence_known_at(evidence)
        prior = current_bound

        if evidence.evidence_id in seen_ids:
            applications.append(EvidenceApplication(
                evidence_id=evidence.evidence_id,
                status=ApplicationStatus.DUPLICATE,
                reason="duplicate_evidence_id",
                known_at=known_at,
                proven_min_f=evidence.proven_min_f,
                prior_bound_f=prior,
                resulting_bound_f=current_bound,
                evidence_type=evidence.evidence_type.value,
            ))
            continue
        seen_ids.add(evidence.evidence_id)

        rejection = _rejection_reason(evidence, station_code=station_code, climate_date=climate_date)
        if rejection is not None:
            applications.append(EvidenceApplication(
                evidence_id=evidence.evidence_id,
                status=ApplicationStatus.REJECTED,
                reason=rejection,
                known_at=known_at,
                proven_min_f=evidence.proven_min_f,
                prior_bound_f=prior,
                resulting_bound_f=current_bound,
                evidence_type=evidence.evidence_type.value,
            ))
            continue

        assert evidence.proven_min_f is not None
        candidate_bound = int(evidence.proven_min_f)
        if current_bound is None or candidate_bound > current_bound:
            current_bound = candidate_bound
            state = HardClimateState(
                state_id=_state_id(
                    station_code,
                    climate_date,
                    candidate_bound,
                    evidence.evidence_id,
                ),
                station_code=station_code,
                climate_date=climate_date,
                proven_daily_high_min_f=candidate_bound,
                first_known_at=known_at,
                transition_evidence_id=evidence.evidence_id,
                supporting_evidence_ids=(evidence.evidence_id,),
                state_model_version=HARD_STATE_ACCUMULATOR_VERSION,
                calendar_version=evidence.calendar_version or CLIMATE_CALENDAR_VERSION,
            )
            states.append(state)
            applications.append(EvidenceApplication(
                evidence_id=evidence.evidence_id,
                status=ApplicationStatus.TRANSITION,
                reason="initial_bound" if prior is None else "raised_bound",
                known_at=known_at,
                proven_min_f=candidate_bound,
                prior_bound_f=prior,
                resulting_bound_f=current_bound,
                evidence_type=evidence.evidence_type.value,
            ))
            continue

        applications.append(EvidenceApplication(
            evidence_id=evidence.evidence_id,
            status=ApplicationStatus.CORROBORATION,
            reason="equal_bound" if candidate_bound == current_bound else "lower_bound_preserved",
            known_at=known_at,
            proven_min_f=candidate_bound,
            prior_bound_f=prior,
            resulting_bound_f=current_bound,
            evidence_type=evidence.evidence_type.value,
        ))

    return HardStateTimeline(
        station_code=station_code,
        climate_date=climate_date,
        applications=tuple(applications),
        states=tuple(states),
    )


def _rejection_reason(
    evidence: SettlementEvidence,
    *,
    station_code: str,
    climate_date: date,
) -> str | None:
    if evidence.station_code != station_code:
        return "station_mismatch"
    if evidence.climate_date != climate_date:
        return "climate_date_mismatch"
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
) -> str:
    raw = "|".join((
        HARD_STATE_ACCUMULATOR_VERSION,
        station_code,
        climate_date.isoformat(),
        str(bound_f),
        transition_evidence_id,
    )).encode("utf-8")
    return f"hard-state:{sha256(raw).hexdigest()[:24]}"
