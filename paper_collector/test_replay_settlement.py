from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from hard_information_domain import HardClimateState
from hard_state_accumulator import HardStateTimeline
from replay_domain import CURRENT_BENCHMARK_VERSIONS, ReplayFilter, ReplayPolicy, build_manifest
from replay_execution import ReplayDecision, ReplayExecutionConfig, ReplayExecutionResult
from replay_hard_state import ReplayHardStateResult, ReplayTransitionElimination
from replay_settlement import LoadedExchangeSettlement, grade_execution
from settlement_audit_domain import build_exchange_market_settlement

UTC = timezone.utc
DAY = date(2026, 8, 21)
STATION = "KNYC"
EVENT = "KXHIGHNY-26AUG21"
MARKET = EVENT + "-B8485"
STATE_ID = "state:replay-test"
ELIM_ID = "elim:replay-test"
RULES = "a" * 64


def dt(hour: int, minute: int = 0, second: int = 0, micros: int = 0) -> datetime:
    return datetime(2026, 8, 21, hour, minute, second, micros, tzinfo=UTC)


def manifest():
    return build_manifest(
        source_session_id="source-session",
        versions=CURRENT_BENCHMARK_VERSIONS,
        policy=ReplayPolicy.BENCHMARK,
        replay_filter=ReplayFilter(station_code=STATION, event_ticker=EVENT, climate_date=DAY),
        events=(),
    )


def config() -> ReplayExecutionConfig:
    return ReplayExecutionConfig(starting_bankroll=Decimal("10"), starting_cash=Decimal("10"))


def hard_state() -> ReplayHardStateResult:
    state = HardClimateState(
        state_id=STATE_ID,
        station_code=STATION,
        climate_date=DAY,
        proven_daily_high_min_f=88,
        first_known_at=dt(18),
        transition_evidence_id="evidence:test",
        supporting_evidence_ids=("evidence:test",),
        state_model_version="hard-state-accumulator-v1",
        calendar_version="lst-climate-calendar-v1",
    )
    timeline = HardStateTimeline(
        station_code=STATION,
        climate_date=DAY,
        calendar_version="lst-climate-calendar-v1",
        applications=(),
        states=(state,),
    )
    elimination = ReplayTransitionElimination(
        state_id=STATE_ID,
        known_at=dt(18),
        rule_snapshot_id=10,
        rule_rules_hash=RULES,
        accepted=True,
        fail_closed_reason=None,
        dead_market_tickers=(MARKET,),
        elimination_payload={
            "event_ticker": EVENT,
            "eliminations": [
                {
                    "elimination_id": ELIM_ID,
                    "event_ticker": EVENT,
                    "market_ticker": MARKET,
                    "station_code": STATION,
                    "climate_date": DAY.isoformat(),
                    "hard_state_id": STATE_ID,
                    "hard_lower_bound_f": 88,
                    "strike_rule": "floor_strike=84;cap_strike=85",
                    "eliminated": True,
                    "elimination_model_version": "bucket-elimination-v1",
                    "reason": "proven_daily_high_exceeds_market_cap",
                }
            ],
        },
    )
    return ReplayHardStateResult(
        manifest_id=manifest().manifest_id,
        station_code=STATION,
        climate_date=DAY,
        evidence=(),
        timeline=timeline,
        eliminations=(elimination,),
        rejected_report_count=0,
        ignored_duplicate_report_count=0,
    )


def execution() -> ReplayExecutionResult:
    m = manifest()
    cfg = config()
    decision = ReplayDecision(
        decision_id="replay-decision:test",
        state_id=STATE_ID,
        elimination_id=ELIM_ID,
        event_ticker=EVENT,
        market_ticker=MARKET,
        decision_at=dt(18),
        simulated_arrival_at=dt(18, 0, 0, 100000),
        decision="trade",
        reason="EXECUTABLE_DEAD_NO_GUARANTEED",
        requested_budget=Decimal("1"),
        filled_qty=Decimal("1"),
        gross_cost=Decimal("0.80"),
        fee=Decimal("0.01"),
        total_cost=Decimal("0.81"),
        guaranteed_profit=Decimal("0.19"),
        guaranteed_roi=Decimal("0.19") / Decimal("0.81"),
        fills=((Decimal("0.80"), Decimal("1")),),
        connection_id="11111111-1111-1111-1111-111111111111",
        snapshot_id=20,
        book_seq=1,
    )
    return ReplayExecutionResult(
        manifest_id=m.manifest_id,
        source_input_sha256=m.source_input_sha256,
        hard_state_output_sha256=hard_state().output_sha256,
        execution_config_sha256=cfg.config_sha256,
        decisions=(decision,),
        ending_cash=Decimal("9.19"),
        positions=(),
        event_deployed=Decimal("0.81"),
        region_deployed=Decimal("0.81"),
        daily_deployed=Decimal("0.81"),
    )


def settlement(result: str, *, rules_hash: str = RULES, captured_at: datetime | None = None) -> LoadedExchangeSettlement:
    item = build_exchange_market_settlement(
        event_ticker=EVENT,
        station_code=STATION,
        climate_date=DAY,
        source_record_id="raw_source_journal:99",
        source_payload_sha256="b" * 64,
        rules_hash=rules_hash,
        rule_source_name="Kalshi test source",
        captured_at=captured_at or dt(23),
        market_results=((MARKET, result),),
    )
    return LoadedExchangeSettlement(settlement=item, settlement_sha256="c" * 64)


class ReplaySettlementTests(unittest.TestCase):
    def grade(self, settlements):
        return grade_execution(
            manifest=manifest(),
            hard_state=hard_state(),
            execution=execution(),
            config=config(),
            settlements=settlements,
        )

    def test_dead_no_settles_no_with_exact_realized_profit(self) -> None:
        grade = self.grade([settlement("no")])
        self.assertEqual(grade.status, "pass")
        self.assertEqual(grade.unsettled_trade_count, 0)
        self.assertEqual(grade.total_payout, Decimal("1"))
        self.assertEqual(grade.settled_cash, Decimal("10.19"))
        self.assertEqual(grade.realized_pnl, Decimal("0.19"))
        self.assertEqual(grade.trade_settlements[0].finding_code, "IMPOSSIBLE_BUCKET_SETTLED_NO")
        self.assertEqual(grade.trade_settlements[0].realized_profit, Decimal("0.19"))

    def test_impossible_bucket_settling_yes_is_invariant_failure_and_loss(self) -> None:
        grade = self.grade([settlement("yes")])
        self.assertEqual(grade.status, "invariant_failure")
        self.assertEqual(grade.settled_cash, Decimal("9.19"))
        self.assertEqual(grade.realized_pnl, Decimal("-0.81"))
        self.assertEqual(grade.trade_settlements[0].finding_code, "IMPOSSIBLE_BUCKET_SETTLED_YES")
        self.assertEqual(grade.trade_settlements[0].realized_profit, Decimal("-0.81"))

    def test_missing_settlement_is_incomplete_without_claiming_realized_pnl(self) -> None:
        grade = self.grade([])
        self.assertEqual(grade.status, "incomplete")
        self.assertEqual(grade.unsettled_trade_count, 1)
        self.assertIsNone(grade.settled_cash)
        self.assertIsNone(grade.realized_pnl)
        self.assertEqual(grade.trade_settlements[0].finding_code, "NO_AUTHORITATIVE_EXCHANGE_SETTLEMENT")

    def test_rule_snapshot_mismatch_is_fail_closed_invariant_failure(self) -> None:
        grade = self.grade([settlement("no", rules_hash="d" * 64)])
        self.assertEqual(grade.status, "invariant_failure")
        self.assertIsNone(grade.realized_pnl)
        self.assertEqual(grade.trade_settlements[0].finding_code, "SETTLEMENT_RULE_SNAPSHOT_MISMATCH")

    def test_settlement_cannot_precede_replay_decision(self) -> None:
        grade = self.grade([settlement("no", captured_at=dt(17, 59))])
        self.assertEqual(grade.status, "invariant_failure")
        self.assertIsNone(grade.realized_pnl)
        self.assertEqual(grade.trade_settlements[0].finding_code, "SETTLEMENT_PRECEDES_REPLAY_DECISION")

    def test_same_inputs_produce_same_settlement_hash(self) -> None:
        one = self.grade([settlement("no")])
        two = self.grade([settlement("no")])
        self.assertEqual(one.to_dict(), two.to_dict())
        self.assertEqual(one.output_sha256, two.output_sha256)


if __name__ == "__main__":
    unittest.main()
