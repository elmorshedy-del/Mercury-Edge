from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://example.invalid/mercury_test")
os.environ.setdefault("PAPER_SESSION_ID", "test-dead-no-session")

import dead_no_executor as executor
import paper_engine as base

UTC = timezone.utc


class FakeResult:
    def __init__(self, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def execute(self, query, params=None):
        text = str(query)
        self.calls.append((text, params))
        if "SELECT 1 FROM paper_portfolio_decisions" in text:
            return FakeResult(None)
        if "INSERT INTO paper_orders" in text:
            return FakeResult((77,))
        if "UPDATE paper_portfolios" in text:
            return FakeResult(None, rowcount=1)
        return FakeResult(None)


def candidate(*, eliminated: bool = True, market_ticker: str = "M-DEAD") -> base.Candidate:
    hard_state = {
        "state_id": "state:phl:2026-08-18:88",
        "station_code": "KPHL",
        "climate_date": "2026-08-18",
        "proven_daily_high_min_f": 88,
        "first_known_at": "2026-08-18T18:55:00+00:00",
        "transition_evidence_id": "ev:88",
        "supporting_evidence_ids": ["ev:88"],
        "state_model_version": "hard-climate-state-v1",
        "calendar_version": "lst-climate-calendar-v1",
    }
    elimination = {
        "elimination_id": "elim:1",
        "event_ticker": "KXHIGHPHIL-26AUG18",
        "market_ticker": market_ticker,
        "station_code": "KPHL",
        "climate_date": "2026-08-18",
        "hard_state_id": hard_state["state_id"],
        "hard_lower_bound_f": 88,
        "strike_rule": "cap_strike=87",
        "eliminated": eliminated,
        "elimination_model_version": "bucket-elimination-v1",
        "reason": "hard_lower_bound_strictly_above_market_cap" if eliminated else "alive",
    }
    return base.Candidate(
        signal_id=41,
        station="KPHL",
        region="northeast",
        event_ticker="KXHIGHPHIL-26AUG18",
        market_ticker=market_ticker,
        trigger_at=datetime(2026, 8, 18, 18, 55, tzinfo=UTC),
        trigger_epoch_ms=int(datetime(2026, 8, 18, 18, 55, tzinfo=UTC).timestamp() * 1000),
        confirmed_high_f=Decimal("88"),
        upper_bound_f=Decimal("87"),
        fee_type="quadratic",
        fee_multiplier=Decimal("1"),
        evidence={
            "event_rules_hash": "rules-1",
            "hard_climate_state": hard_state,
            "bucket_elimination": elimination,
            "elimination_context": {
                "event_ticker": "KXHIGHPHIL-26AUG18",
                "station_code": "KPHL",
                "climate_date": "2026-08-18",
                "hard_state_id": hard_state["state_id"],
                "transition_evidence_id": "ev:88",
                "event_rules_hash": "rules-1",
                "elimination_model_version": "bucket-elimination-v1",
                "dead_market_tickers": [market_ticker] if eliminated else [],
            },
        },
    )


def mode(**config):
    merged = {
        "execution_latency_ms": 100,
        "max_no_price": "1.00",
        "allocation": "best_edge_first",
    }
    merged.update(config)
    return {
        "portfolio_id": 9,
        "mode_code": "test",
        "config": merged,
    }


class CanonicalDeadGuardTests(unittest.TestCase):
    def test_non_dead_candidate_is_rejected_before_market_lookup(self) -> None:
        conn = FakeConnection()
        c = candidate(eliminated=False)
        with patch.object(base, "mode_budget", return_value=Decimal("50")), \
             patch.object(base, "reconstruct_book") as reconstruct, \
             patch.object(base, "record_decision", return_value=True) as record:
            item = executor.evaluate_for_mode(conn, c, mode(), {})
        self.assertIsNone(item)
        reconstruct.assert_not_called()
        self.assertEqual(record.call_args.args[5], "MARKET_NOT_CANONICALLY_ELIMINATED")

    def test_mismatched_market_elimination_is_rejected(self) -> None:
        c = candidate()
        c.evidence["bucket_elimination"]["market_ticker"] = "OTHER"
        self.assertEqual(executor.canonical_dead_reason(c), "ELIMINATION_MARKET_MISMATCH")

    def test_missing_elimination_proof_is_rejected(self) -> None:
        c = candidate()
        del c.evidence["bucket_elimination"]
        self.assertEqual(executor.canonical_dead_reason(c), "MISSING_CANONICAL_ELIMINATION_PROOF")


class LiveL2EvaluationTests(unittest.TestCase):
    def test_dead_candidate_uses_causal_l2_and_exact_fee_adjusted_plan(self) -> None:
        conn = FakeConnection()
        c = candidate()
        book = base.BookState(
            yes_bids={Decimal("0.40"): Decimal("100")},
            connection_id="conn-1",
            last_seq=8,
            snapshot_id=12,
            snapshot_received_ms=c.trigger_epoch_ms,
        )
        with patch.object(base, "mode_budget", return_value=Decimal("50")), \
             patch.object(base, "reconstruct_book", return_value=book) as reconstruct:
            item = executor.evaluate_for_mode(conn, c, mode(), {})
        self.assertIsNotNone(item)
        assert item is not None
        reconstruct.assert_called_once_with(conn, c.market_ticker, c.trigger_epoch_ms + 100)
        self.assertEqual(item.best_ask, Decimal("0.60"))
        self.assertGreater(item.plan.guaranteed_profit, 0)
        self.assertGreater(item.plan.guaranteed_roi, 0)
        self.assertLessEqual(item.plan.total_cost, Decimal("50"))

    def test_fee_adjusted_zero_edge_is_skipped_even_with_l2(self) -> None:
        conn = FakeConnection()
        c = candidate()
        book = base.BookState(
            yes_bids={Decimal("0.00"): Decimal("10")},  # executable NO ask = $1
            connection_id="conn-1",
            last_seq=8,
            snapshot_id=12,
            snapshot_received_ms=c.trigger_epoch_ms,
        )
        with patch.object(base, "mode_budget", return_value=Decimal("10")), \
             patch.object(base, "reconstruct_book", return_value=book), \
             patch.object(base, "record_decision", return_value=True) as record:
            item = executor.evaluate_for_mode(conn, c, mode(), {})
        self.assertIsNone(item)
        self.assertEqual(record.call_args.args[5], "NO_POSITIVE_GUARANTEED_RETURN_AFTER_FEES")

    def test_no_l2_is_blocked_without_proxy_fallback(self) -> None:
        conn = FakeConnection()
        c = candidate()
        with patch.object(base, "mode_budget", return_value=Decimal("50")), \
             patch.object(base, "reconstruct_book", return_value=None), \
             patch.object(base, "record_decision", return_value=True) as record:
            item = executor.evaluate_for_mode(conn, c, mode(), {})
        self.assertIsNone(item)
        self.assertEqual(record.call_args.args[5], "NO_VALID_L2_AT_SIMULATED_ARRIVAL")


class PaperOrderWriteTests(unittest.TestCase):
    def test_order_audit_records_guaranteed_economics_and_exact_elimination(self) -> None:
        conn = FakeConnection()
        c = candidate()
        book = base.BookState(
            yes_bids={Decimal("0.40"): Decimal("100")},
            connection_id="conn-1",
            last_seq=8,
            snapshot_id=12,
            snapshot_received_ms=c.trigger_epoch_ms,
        )
        plan = executor.dne.plan_dead_no(
            [(Decimal("0.60"), Decimal("100"))],
            budget=Decimal("50"),
            fee_multiplier=Decimal("1"),
            max_price=Decimal("1.00"),
        )
        assert plan is not None
        evaluated = executor.EvaluatedDeadNo(
            candidate=c,
            book=book,
            asks=((Decimal("0.60"), Decimal("100")),),
            plan=plan,
            latency_ms=100,
            max_price=Decimal("1.00"),
            budget_at_evaluation=Decimal("50"),
            depth_notional=Decimal("60"),
        )
        with patch.object(base, "record_decision", return_value=True) as record:
            cost = executor._place_order(conn, mode(), evaluated, Decimal("50"))
        self.assertGreater(cost, 0)
        self.assertEqual(record.call_args.args[5], "EXECUTABLE_DEAD_NO_GUARANTEED")
        self.assertEqual(record.call_args.args[7], plan.guaranteed_roi)

        order_calls = [(q, p) for q, p in conn.calls if "INSERT INTO paper_orders" in q]
        self.assertEqual(len(order_calls), 1)
        params = order_calls[0][1]
        audit = json.loads(params[15])
        guarantee = audit["execution_guarantee"]
        self.assertEqual(guarantee["execution_model_version"], executor.EXECUTION_MODEL_VERSION)
        self.assertEqual(guarantee["execution_math_version"], executor.dne.EXECUTION_MATH_VERSION)
        self.assertEqual(guarantee["guaranteed_profit"], format(plan.guaranteed_profit, "f"))
        self.assertEqual(audit["bucket_elimination"]["elimination_id"], "elim:1")

        book_snapshot = json.loads(params[14])
        self.assertTrue(book_snapshot["l2_only"])


if __name__ == "__main__":
    unittest.main()
