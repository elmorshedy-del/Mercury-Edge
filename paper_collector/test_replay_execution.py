from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256

from hard_state_accumulator import HardStateTimeline
from replay_domain import CURRENT_BENCHMARK_VERSIONS, ReplayFilter, ReplayPolicy, build_manifest
from replay_execution import (
    ReplayCounterfactualResult,
    ReplayExecutionConfig,
    ReplayMarketRow,
    UnsupportedReplayVersion,
    audit_chain_hash,
    execute_replay,
    reconstruct_book,
)
from replay_hard_state import ReplayHardStateResult, ReplayTransitionElimination

UTC = timezone.utc
DAY = date(2026, 8, 21)
EVENT = "KXHIGHNY-26AUG21"
MARKET = EVENT + "-B8687"
MARKET2 = EVENT + "-B8485"


def dt(second: float = 0) -> datetime:
    base = datetime(2026, 8, 21, 18, 0, tzinfo=UTC)
    return datetime.fromtimestamp(base.timestamp() + second, tz=UTC)


def manifest(execution_version: str | None = None):
    versions = CURRENT_BENCHMARK_VERSIONS
    if execution_version is not None:
        versions = replace(versions, execution_version=execution_version)
    return build_manifest(
        source_session_id="source-session",
        versions=versions,
        policy=ReplayPolicy.BENCHMARK,
        replay_filter=ReplayFilter(station_code="KNYC", event_ticker=EVENT, climate_date=DAY),
        events=(),
    )


def hard_result(markets=(MARKET,), known_at=None):
    known = known_at or dt(0)
    elimination_rows = []
    dead = []
    for index, market in enumerate(markets):
        eid = f"elim-{index}"
        dead.append(market)
        elimination_rows.append({
            "elimination_id": eid,
            "event_ticker": EVENT,
            "market_ticker": market,
            "station_code": "KNYC",
            "climate_date": DAY.isoformat(),
            "hard_state_id": "state-88",
            "hard_lower_bound_f": 88,
            "strike_rule": "cap_strike=87",
            "eliminated": True,
            "elimination_model_version": "bucket-elimination-v1",
            "reason": "hard_lower_bound_strictly_above_market_cap",
        })
    payload = {
        "event_ticker": EVENT,
        "station_code": "KNYC",
        "climate_date": DAY.isoformat(),
        "hard_state_id": "state-88",
        "transition_evidence_id": "evidence-88",
        "event_rules_hash": "a" * 64,
        "accepted": True,
        "fail_closed_reason": None,
        "elimination_model_version": "bucket-elimination-v1",
        "eliminations": elimination_rows,
        "dead_market_tickers": dead,
    }
    timeline = HardStateTimeline(
        station_code="KNYC", climate_date=DAY,
        calendar_version="lst-climate-calendar-v1", applications=(), states=(),
    )
    return ReplayHardStateResult(
        manifest_id=manifest().manifest_id,
        station_code="KNYC", climate_date=DAY,
        evidence=(), timeline=timeline,
        eliminations=(ReplayTransitionElimination(
            state_id="state-88", known_at=known,
            rule_snapshot_id=1, rule_rules_hash="a" * 64,
            accepted=True, fail_closed_reason=None,
            dead_market_tickers=tuple(dead), elimination_payload=payload,
        ),),
        rejected_report_count=0, ignored_duplicate_report_count=0,
    )


def raw_snapshot(market: str, yes_price: str = "0.20", qty: str = "10") -> str:
    return json.dumps({"msg": {
        "market_ticker": market,
        "yes_dollars_fp": [[yes_price, qty]],
        "no_dollars_fp": [[str(Decimal("1") - Decimal(yes_price)), qty]],
    }}, separators=(",", ":"))


def row(row_id: int, market: str, received: datetime, *, seq: int, raw_text: str, prev: str | None = None, connection="11111111-1111-1111-1111-111111111111") -> ReplayMarketRow:
    digest = sha256(raw_text.encode()).hexdigest()
    epoch_ns = int(received.timestamp() * 1_000_000_000)
    chain = audit_chain_hash(prev, connection, epoch_ns, digest)
    return ReplayMarketRow(
        row_id=row_id, channel="orderbook_snapshot", sid=1, seq=seq,
        market_ticker=market, received_at=received,
        received_epoch_ms=epoch_ns // 1_000_000, received_epoch_ns=epoch_ns,
        raw_text=raw_text, payload_sha256=digest, connection_id=connection,
        prev_chain_hash=prev, chain_hash=chain, price_mode=None,
    )


def config(latency=100, **kwargs):
    base = dict(
        starting_bankroll=Decimal("100"), starting_cash=Decimal("100"),
        execution_latency_ms=latency, fee_multiplier=Decimal("1"),
        max_no_price=Decimal("1"), allocation="best_edge_first",
    )
    base.update(kwargs)
    return ReplayExecutionConfig(**base)


class BookReplayTests(unittest.TestCase):
    def test_no_l2_at_arrival_does_not_use_future_snapshot(self) -> None:
        snap = row(1, MARKET, dt(.200), seq=1, raw_text=raw_snapshot(MARKET))
        book, reason = reconstruct_book([snap], market_ticker=MARKET, arrival_ms=int(dt(.100).timestamp() * 1000))
        self.assertIsNone(book)
        self.assertEqual(reason, "NO_VALID_L2_AT_SIMULATED_ARRIVAL")

    def test_sequence_gap_invalidates_connection(self) -> None:
        first = row(1, MARKET, dt(.050), seq=1, raw_text=raw_snapshot(MARKET))
        second_raw = raw_snapshot(MARKET, "0.25")
        second = row(2, MARKET, dt(.080), seq=3, raw_text=second_raw, prev=first.chain_hash)
        book, reason = reconstruct_book([first, second], market_ticker=MARKET, arrival_ms=int(dt(.100).timestamp() * 1000))
        self.assertIsNone(book)
        self.assertEqual(reason, "L2_CONNECTION_INTEGRITY_FAILURE")

    def test_payload_hash_mismatch_fails_connection_closed(self) -> None:
        first = row(1, MARKET, dt(.050), seq=1, raw_text=raw_snapshot(MARKET))
        bad = replace(first, payload_sha256="0" * 64)
        book, reason = reconstruct_book([bad], market_ticker=MARKET, arrival_ms=int(dt(.100).timestamp() * 1000))
        self.assertIsNone(book)
        self.assertEqual(reason, "L2_CONNECTION_INTEGRITY_FAILURE")


class ExecutionReplayTests(unittest.TestCase):
    def test_same_inputs_config_replay_twice_is_byte_deterministic(self) -> None:
        snap = row(1, MARKET, dt(.050), seq=1, raw_text=raw_snapshot(MARKET))
        h = hard_result()
        a = execute_replay(manifest=manifest(), hard_state=h, market_rows=[snap], config=config())
        b = execute_replay(manifest=manifest(), hard_state=h, market_rows=[snap], config=config())
        self.assertEqual(a.output_sha256, b.output_sha256)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(a.decisions[0].decision, "trade")
        self.assertGreater(a.decisions[0].guaranteed_profit, 0)

    def test_later_l2_cannot_fill_earlier_arrival(self) -> None:
        snap = row(1, MARKET, dt(.150), seq=1, raw_text=raw_snapshot(MARKET))
        result = execute_replay(manifest=manifest(), hard_state=hard_result(), market_rows=[snap], config=config(latency=100))
        self.assertEqual(result.decisions[0].decision, "blocked")
        self.assertEqual(result.decisions[0].reason, "NO_VALID_L2_AT_SIMULATED_ARRIVAL")
        self.assertEqual(result.ending_cash, Decimal("100"))

    def test_latency_ab_changes_execution_but_not_source_input_hash(self) -> None:
        snap = row(1, MARKET, dt(.150), seq=1, raw_text=raw_snapshot(MARKET))
        m = manifest()
        early = execute_replay(manifest=m, hard_state=hard_result(), market_rows=[snap], config=config(latency=100))
        late = execute_replay(manifest=m, hard_state=hard_result(), market_rows=[snap], config=config(latency=200))
        self.assertEqual(early.source_input_sha256, late.source_input_sha256)
        self.assertNotEqual(early.execution_config_sha256, late.execution_config_sha256)
        self.assertNotEqual(early.output_sha256, late.output_sha256)
        self.assertEqual(early.decisions[0].decision, "blocked")
        self.assertEqual(late.decisions[0].decision, "trade")

    def test_capital_cap_is_applied_to_exact_fee_aware_plan(self) -> None:
        snap = row(1, MARKET, dt(.050), seq=1, raw_text=raw_snapshot(MARKET, "0.20", "100"))
        result = execute_replay(
            manifest=manifest(), hard_state=hard_result(), market_rows=[snap],
            config=config(max_trade_pct=Decimal("0.10")),
        )
        trade = result.decisions[0]
        self.assertEqual(trade.decision, "trade")
        self.assertLessEqual(trade.total_cost, Decimal("10"))
        self.assertEqual(result.ending_cash, Decimal("100") - trade.total_cost)

    def test_two_dead_markets_create_two_independent_positions_without_overspend(self) -> None:
        first = row(1, MARKET, dt(.050), seq=1, raw_text=raw_snapshot(MARKET, "0.20", "20"))
        raw2 = raw_snapshot(MARKET2, "0.25", "20")
        digest2 = sha256(raw2.encode()).hexdigest()
        epoch_ns = int(dt(.060).timestamp() * 1_000_000_000)
        chain2 = audit_chain_hash(first.chain_hash, first.connection_id or "", epoch_ns, digest2)
        second = ReplayMarketRow(
            row_id=2, channel="orderbook_snapshot", sid=2, seq=1,
            market_ticker=MARKET2, received_at=dt(.060), received_epoch_ms=epoch_ns // 1_000_000,
            received_epoch_ns=epoch_ns, raw_text=raw2, payload_sha256=digest2,
            connection_id=first.connection_id, prev_chain_hash=first.chain_hash, chain_hash=chain2,
        )
        result = execute_replay(
            manifest=manifest(), hard_state=hard_result((MARKET, MARKET2)), market_rows=[first, second],
            config=config(max_trade_pct=Decimal("0.50"), max_event_pct=Decimal("1")),
        )
        self.assertEqual(len([d for d in result.decisions if d.decision == "trade"]), 2)
        self.assertEqual(len(result.positions), 2)
        self.assertGreaterEqual(result.ending_cash, 0)

    def test_unknown_execution_version_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(UnsupportedReplayVersion, "UNSUPPORTED_VERSION"):
            execute_replay(
                manifest=manifest("future-execution-v99"), hard_state=hard_result(), market_rows=[], config=config()
            )

    def test_research_counterfactual_type_is_separate_from_benchmark_result(self) -> None:
        research = ReplayCounterfactualResult("latency-250ms", "abc", {"hypothetical": True})
        self.assertEqual(research.label, "latency-250ms")
        with self.assertRaises(ValueError):
            config(benchmark_label="research")


if __name__ == "__main__":
    unittest.main()
