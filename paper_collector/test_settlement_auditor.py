from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import unittest

from hard_information_domain import BucketElimination, HardClimateState
from settlement_audit_domain import BenchmarkTradeProof, build_exchange_market_settlement
from settlement_auditor import (
    audit_exchange_market_result,
    audit_hard_state_against_final_max,
    audit_validation_against_final_max,
    benchmark_trade_proof_from_order,
    normalize_exchange_capture_for_trade,
)
from settlement_validation import build_authoritative_settlement, parse_nws_cli
from validation_collector import SettledEventCapture

UTC = timezone.utc
DAY = date(2026, 8, 21)
RULES_HASH = "c" * 64
RAW_HASH = "d" * 64
EVENT = "KXHIGHNY-26AUG21"
MARKET = "KXHIGHNY-26AUG21-T87"


def state(bound: int = 88) -> HardClimateState:
    return HardClimateState(
        state_id=f"state:{bound}",
        station_code="KNYC",
        climate_date=DAY,
        proven_daily_high_min_f=bound,
        first_known_at=datetime(2026, 8, 21, 18, 0, tzinfo=UTC),
        transition_evidence_id=f"evidence:{bound}",
        supporting_evidence_ids=(f"evidence:{bound}", "evidence:corroborating"),
        state_model_version="hard-state-accumulator-v1",
        calendar_version="lst-v1",
    )


def elimination(hard_state: HardClimateState | None = None) -> BucketElimination:
    hard_state = hard_state or state()
    return BucketElimination(
        elimination_id="elimination:dead-87",
        event_ticker=EVENT,
        market_ticker=MARKET,
        station_code="KNYC",
        climate_date=DAY,
        hard_state_id=hard_state.state_id,
        hard_lower_bound_f=hard_state.proven_daily_high_min_f,
        strike_rule="winning_temperature<=87",
        eliminated=True,
        elimination_model_version="bucket-elimination-v1",
        reason="hard_lower_bound_exceeds_market_maximum",
    )


def trade(bound: int = 88) -> BenchmarkTradeProof:
    hard_state = state(bound)
    return BenchmarkTradeProof(
        session_id="s",
        order_id=501,
        outcome_side="no",
        event_rules_hash=RULES_HASH,
        hard_state=hard_state,
        elimination=elimination(hard_state),
    )


def exchange(result: str = "no"):
    return build_exchange_market_settlement(
        event_ticker=EVENT,
        station_code="KNYC",
        climate_date=DAY,
        source_record_id="raw_source_journal:900",
        source_payload_sha256=RAW_HASH,
        rules_hash=RULES_HASH,
        rule_source_name="The Weather Company",
        captured_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        market_results=((MARKET, result), ("OTHER", "yes" if result == "no" else "no")),
    )


def numeric_settlement(final_max: int, *, raw_id: int = 901, revision_of: str | None = None):
    return build_authoritative_settlement(
        event_ticker=EVENT,
        station_code="KNYC",
        climate_day=DAY,
        final_max_f=final_max,
        source_record_id=f"raw_source_journal:{raw_id}",
        observed_or_issued_at=datetime(2026, 8, 22, 13, 0, tzinfo=UTC),
        rules_hash=RULES_HASH,
        rule_source_name="The Weather Company",
        settlement_source_name="The Weather Company",
        revision_of=revision_of,
    )


def validation(max_f: int = 88):
    issued = datetime(2026, 8, 22, 5, 30, tzinfo=UTC)
    return parse_nws_cli(
        f"CLIMATE SUMMARY FOR AUGUST 21 2026\nMAXIMUM {max_f}\n",
        source_product_id=f"cli:{max_f}",
        station_code="KNYC",
        timezone_name="America/New_York",
        issued_at=issued,
        mercury_received_at=issued,
        source_record_id=f"raw_source_journal:{700 + max_f}",
        source_payload_sha256="a" * 64,
    )


class TradeProofTests(unittest.TestCase):
    def test_trade_proof_requires_exact_hard_state_elimination_identity(self) -> None:
        bad = replace(elimination(), hard_state_id="state:other")
        with self.assertRaisesRegex(ValueError, "hard-state identity"):
            BenchmarkTradeProof(
                session_id="s",
                order_id=1,
                outcome_side="no",
                event_rules_hash=RULES_HASH,
                hard_state=state(),
                elimination=bad,
            )

    def test_non_eliminated_market_cannot_become_hard_edge_trade_proof(self) -> None:
        with self.assertRaisesRegex(ValueError, "eliminated market"):
            BenchmarkTradeProof(
                session_id="s",
                order_id=1,
                outcome_side="no",
                event_rules_hash=RULES_HASH,
                hard_state=state(),
                elimination=replace(elimination(), eliminated=False),
            )


class ExchangeInvariantTests(unittest.TestCase):
    def test_eliminated_market_settling_no_passes(self) -> None:
        result = audit_exchange_market_result(trade=trade(), settlement=exchange("no"))
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.severity, "info")
        self.assertEqual(result.finding_code, "IMPOSSIBLE_BUCKET_SETTLED_NO")
        self.assertEqual(result.order_id, 501)
        self.assertEqual(result.details["transition_evidence_id"], "evidence:88")
        self.assertIn("evidence:corroborating", result.details["supporting_evidence_ids"])

    def test_eliminated_market_settling_yes_is_critical_invariant_failure(self) -> None:
        result = audit_exchange_market_result(trade=trade(), settlement=exchange("yes"))
        self.assertEqual(result.status, "invariant_failure")
        self.assertEqual(result.severity, "critical")
        self.assertEqual(result.finding_code, "IMPOSSIBLE_BUCKET_SETTLED_YES")
        self.assertEqual(result.market_ticker, MARKET)
        self.assertEqual(result.details["exchange_raw_source_id"], "raw_source_journal:900")

    def test_rule_snapshot_mismatch_fails_closed(self) -> None:
        wrong = replace(exchange("no"), rules_hash="e" * 64)
        with self.assertRaisesRegex(ValueError, "rule-snapshot"):
            audit_exchange_market_result(trade=trade(), settlement=wrong)

    def test_station_date_event_and_market_identity_are_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            audit_exchange_market_result(
                trade=trade(),
                settlement=replace(exchange("no"), station_code="KPHL"),
            )
        missing_market = build_exchange_market_settlement(
            event_ticker=EVENT,
            station_code="KNYC",
            climate_date=DAY,
            source_record_id="raw_source_journal:900",
            source_payload_sha256=RAW_HASH,
            rules_hash=RULES_HASH,
            rule_source_name="The Weather Company",
            captured_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
            market_results=(("OTHER", "no"),),
        )
        with self.assertRaisesRegex(ValueError, "does not contain traded market"):
            audit_exchange_market_result(trade=trade(), settlement=missing_market)

    def test_same_exchange_inputs_are_deterministic(self) -> None:
        first = audit_exchange_market_result(trade=trade(), settlement=exchange("no"))
        second = audit_exchange_market_result(trade=trade(), settlement=exchange("no"))
        self.assertEqual(first.audit_id, second.audit_id)
        self.assertEqual(first.to_dict(), second.to_dict())


class NumericFinalMaxInvariantTests(unittest.TestCase):
    def test_final_max_at_or_above_hard_bound_passes(self) -> None:
        result = audit_hard_state_against_final_max(
            trade=trade(88),
            settlement=numeric_settlement(88),
        )
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.finding_code, "HARD_STATE_CONFIRMED_BY_FINAL_MAX")

    def test_final_max_below_traded_hard_bound_is_critical(self) -> None:
        result = audit_hard_state_against_final_max(
            trade=trade(88),
            settlement=numeric_settlement(87),
        )
        self.assertEqual(result.status, "invariant_failure")
        self.assertEqual(result.severity, "critical")
        self.assertEqual(result.finding_code, "HARD_STATE_EXCEEDS_FINAL_MAX")
        self.assertEqual(result.details["hard_lower_bound_f"], 88)
        self.assertEqual(result.details["final_max_f"], 87)

    def test_numeric_truth_wrong_event_or_rules_fails_closed(self) -> None:
        wrong_rules = replace(numeric_settlement(88), rules_hash="f" * 64)
        with self.assertRaisesRegex(ValueError, "rule-snapshot"):
            audit_hard_state_against_final_max(trade=trade(), settlement=wrong_rules)

    def test_revised_truth_creates_distinct_audit_identity_without_mutation(self) -> None:
        first_settlement = numeric_settlement(88, raw_id=901)
        first = audit_hard_state_against_final_max(trade=trade(), settlement=first_settlement)
        revised_settlement = numeric_settlement(
            87,
            raw_id=902,
            revision_of=first_settlement.truth.truth_id,
        )
        revised = audit_hard_state_against_final_max(trade=trade(), settlement=revised_settlement)
        self.assertNotEqual(first_settlement.settlement_id, revised_settlement.settlement_id)
        self.assertNotEqual(first.audit_id, revised.audit_id)
        self.assertEqual(first.status, "pass")
        self.assertEqual(revised.status, "invariant_failure")


class NonAuthoritativeValidationTests(unittest.TestCase):
    def test_nws_disagreement_is_warning_not_contract_invariant_failure(self) -> None:
        result = audit_validation_against_final_max(
            session_id="s",
            validation=validation(87),
            settlement=numeric_settlement(88),
        )
        self.assertEqual(result.status, "discrepancy")
        self.assertEqual(result.severity, "warning")
        self.assertEqual(result.finding_code, "NON_AUTHORITATIVE_VALIDATION_DISAGREEMENT")
        self.assertFalse(result.details["classified_as_contract_invariant"])

    def test_nws_agreement_is_corroborating_pass_only(self) -> None:
        result = audit_validation_against_final_max(
            session_id="s",
            validation=validation(88),
            settlement=numeric_settlement(88),
        )
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.finding_code, "NON_AUTHORITATIVE_VALIDATION_AGREES")

    def test_validation_station_or_date_mismatch_fails_closed(self) -> None:
        wrong_station = replace(validation(88), station_code="KPHL")
        with self.assertRaisesRegex(ValueError, "station mismatch"):
            audit_validation_against_final_max(
                session_id="s",
                validation=wrong_station,
                settlement=numeric_settlement(88),
            )


class FakeOrderConnection:
    def __init__(self, trade_proof: BenchmarkTradeProof):
        self.trade = trade_proof
        self.raw_row = ("s", "KNYC", datetime(2026, 8, 22, 12, 0, tzinfo=UTC), RAW_HASH)

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        if text.startswith("SELECT o.session_id,o.market_ticker,o.outcome_side,o.audit"):
            audit = {
                "bucket_elimination": self.trade.elimination.to_dict(),
                "hard_climate_state": self.trade.hard_state.to_dict(),
                "event_rules_hash": self.trade.event_rules_hash,
            }
            return _Result(("s", MARKET, "no", audit, EVENT, "KNYC"))
        if text.startswith("SELECT session_id,station_code,received_at,payload_sha256 FROM raw_source_journal"):
            return _Result(self.raw_row)
        if text.startswith("SELECT rules_hash,settlement_sources FROM settlement_rule_snapshots"):
            return _Result((RULES_HASH, [{"name": "The Weather Company", "url": "https://example.test"}]))
        raise AssertionError(f"unexpected SQL: {text}")


class _Result:
    def __init__(self, one):
        self.one = one

    def fetchone(self):
        return self.one


class DatabaseTraceTests(unittest.TestCase):
    def test_paper_order_rehydrates_exact_canonical_trade_proof(self) -> None:
        original = trade()
        restored = benchmark_trade_proof_from_order(FakeOrderConnection(original), order_id=501)
        self.assertEqual(restored, original)

    def test_exchange_capture_normalization_requires_exact_trade_rule_snapshot(self) -> None:
        original = trade()
        capture = SettledEventCapture(
            raw_source_id=900,
            event_ticker=EVENT,
            station_code="KNYC",
            payload_sha256=RAW_HASH,
            fully_resolved=True,
            market_results=((MARKET, "no"), ("OTHER", "yes")),
            fail_closed_reason=None,
        )
        settlement = normalize_exchange_capture_for_trade(
            FakeOrderConnection(original),
            trade=original,
            capture=capture,
        )
        self.assertEqual(settlement.rules_hash, RULES_HASH)
        self.assertEqual(settlement.rule_source_name, "The Weather Company")
        self.assertEqual(settlement.source_record_id, "raw_source_journal:900")
        self.assertEqual(settlement.result_for(MARKET), "no")


if __name__ == "__main__":
    unittest.main()
