from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import unittest

from hard_information_domain import (
    EvidenceTrust,
    EvidenceType,
    IntegrityStatus,
    SettlementEvidence,
    SourceClocks,
)
from hard_state_accumulator import ApplicationStatus, accumulate_hard_state
from market_calendar import CLIMATE_CALENDAR_VERSION

UTC = timezone.utc
DAY = date(2026, 8, 18)
BASE = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)


def evidence(
    evidence_id: str,
    bound: int,
    *,
    minutes: int,
    observed_minutes: int | None = None,
    station: str = "KPHL",
    climate_day: date = DAY,
    evidence_type: EvidenceType = EvidenceType.ASOS_T_GROUP_CURRENT,
    trust: EvidenceTrust = EvidenceTrust.BENCHMARK_ELIGIBLE,
    integrity: IntegrityStatus = IntegrityStatus.CANONICAL,
    calendar_version: str = CLIMATE_CALENDAR_VERSION,
) -> SettlementEvidence:
    received = BASE + timedelta(minutes=minutes)
    observed = BASE + timedelta(minutes=observed_minutes if observed_minutes is not None else minutes - 1)
    return SettlementEvidence(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        station_code=station,
        climate_date=climate_day,
        source_record_ids=(f"raw:{evidence_id}",),
        proven_min_f=bound,
        proven_max_f=bound,
        integrity_status=integrity,
        trust=trust,
        clocks=SourceClocks(observed_at=observed, mercury_received_at=received),
        parser_version="test-parser-v1",
        evidence_model_version="test-evidence-v1",
        calendar_version=calendar_version,
        raw_identifier=evidence_id,
        possible_canonical_f=(bound,),
    )


class HardStateAccumulatorTests(unittest.TestCase):
    def test_monotonic_transitions_never_fall_after_lower_later_observation(self) -> None:
        stream = [
            evidence("e86", 86, minutes=1),
            evidence("e87", 87, minutes=2),
            evidence("e88", 88, minutes=3),
            evidence("e85-late", 85, minutes=4),
        ]
        timeline = accumulate_hard_state(stream, station_code="KPHL", climate_date=DAY)
        self.assertEqual([state.proven_daily_high_min_f for state in timeline.states], [86, 87, 88])
        self.assertEqual(timeline.current_bound_f, 88)
        late = timeline.application_for("e85-late")
        assert late is not None
        self.assertEqual(late.status, ApplicationStatus.CORROBORATION)
        self.assertEqual(late.reason, "lower_bound_preserved")
        self.assertEqual(late.resulting_bound_f, 88)

    def test_equal_six_hour_disclosure_is_corroboration_not_new_transition(self) -> None:
        precise = evidence("t-88", 88, minutes=1, evidence_type=EvidenceType.ASOS_T_GROUP_CURRENT)
        six_hour = evidence("six-88", 88, minutes=20, evidence_type=EvidenceType.ASOS_SIX_HOUR_MAX)
        timeline = accumulate_hard_state([precise, six_hour], station_code="KPHL", climate_date=DAY)
        self.assertEqual(len(timeline.states), 1)
        self.assertEqual(timeline.states[0].transition_evidence_id, "t-88")
        later = timeline.application_for("six-88")
        assert later is not None
        self.assertEqual(later.status, ApplicationStatus.CORROBORATION)
        self.assertEqual(later.reason, "equal_bound")
        self.assertEqual(later.known_at, six_hour.clocks.mercury_received_at)

    def test_one_network_receipt_with_current_and_hidden_max_is_one_atomic_transition(self) -> None:
        # Both facts became usable in the same response. Mercury must jump
        # directly to 77 rather than fabricate tradeable 73/74 intermediates.
        current = evidence(
            "current-74",
            74,
            minutes=10,
            evidence_type=EvidenceType.ASOS_T_GROUP_CURRENT,
        )
        hidden = evidence(
            "six-hour-77",
            77,
            minutes=10,
            evidence_type=EvidenceType.ASOS_SIX_HOUR_MAX,
        )
        lossy_main = evidence(
            "main-73",
            73,
            minutes=10,
            evidence_type=EvidenceType.ASOS_MAIN_CURRENT,
            integrity=IntegrityStatus.AMBIGUOUS,
        )
        timeline = accumulate_hard_state(
            [current, hidden, lossy_main],
            station_code="KPHL",
            climate_date=DAY,
        )
        self.assertEqual([state.proven_daily_high_min_f for state in timeline.states], [77])
        self.assertEqual(timeline.states[0].transition_evidence_id, "six-hour-77")
        self.assertEqual(timeline.application_for("current-74").reason, "same_batch_lower_bound")
        self.assertEqual(timeline.application_for("main-73").reason, "same_batch_lower_bound")

    def test_exact_station_date_calendar_and_trust_are_required(self) -> None:
        wrong_station = evidence("wrong-station", 99, minutes=1, station="KLAX")
        wrong_day = evidence("wrong-day", 99, minutes=2, climate_day=date(2026, 8, 17))
        wrong_calendar = evidence("wrong-calendar", 99, minutes=3, calendar_version="civil-midnight-v0")
        validation = evidence(
            "validation",
            99,
            minutes=4,
            evidence_type=EvidenceType.CLI_MAX,
            trust=EvidenceTrust.VALIDATION_ONLY,
        )
        good = evidence("good", 88, minutes=5)
        timeline = accumulate_hard_state(
            [wrong_station, wrong_day, wrong_calendar, validation, good],
            station_code="KPHL",
            climate_date=DAY,
        )
        self.assertEqual(timeline.current_bound_f, 88)
        reasons = {item.evidence_id: item.reason for item in timeline.applications}
        self.assertEqual(reasons["wrong-station"], "station_mismatch")
        self.assertEqual(reasons["wrong-day"], "climate_date_mismatch")
        self.assertEqual(reasons["wrong-calendar"], "calendar_version_mismatch")
        self.assertEqual(reasons["validation"], "not_benchmark_eligible")

    def test_mercury_receipt_not_observation_time_controls_causal_order(self) -> None:
        # The hotter observation physically occurred first but Mercury received
        # it later. Replay must not use it before receipt.
        hot_late = evidence("hot-late", 90, minutes=10, observed_minutes=0)
        cool_early = evidence("cool-early", 87, minutes=5, observed_minutes=4)
        timeline = accumulate_hard_state(
            [hot_late, cool_early],
            station_code="KPHL",
            climate_date=DAY,
        )
        self.assertEqual([state.proven_daily_high_min_f for state in timeline.states], [87, 90])
        self.assertEqual(timeline.states[0].first_known_at, cool_early.clocks.mercury_received_at)
        self.assertEqual(timeline.states[1].first_known_at, hot_late.clocks.mercury_received_at)

    def test_duplicate_evidence_id_cannot_retrigger(self) -> None:
        first = evidence("same", 88, minutes=1)
        duplicate = evidence("same", 88, minutes=2)
        timeline = accumulate_hard_state(
            [duplicate, first],
            station_code="KPHL",
            climate_date=DAY,
        )
        self.assertEqual(len(timeline.states), 1)
        duplicates = [item for item in timeline.applications if item.status is ApplicationStatus.DUPLICATE]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].evidence_id, "same")

    def test_same_stream_is_deterministic_independent_of_input_order(self) -> None:
        stream = [
            evidence("a", 86, minutes=1),
            evidence("b", 88, minutes=2),
            evidence("c", 87, minutes=3),
        ]
        a = accumulate_hard_state(stream, station_code="KPHL", climate_date=DAY)
        b = accumulate_hard_state(list(reversed(stream)), station_code="KPHL", climate_date=DAY)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(json.dumps(a.to_dict(), sort_keys=True), json.dumps(b.to_dict(), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
