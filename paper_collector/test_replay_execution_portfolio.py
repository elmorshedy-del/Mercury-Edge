from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256

from hard_state_accumulator import HardStateTimeline
from replay_domain import CURRENT_BENCHMARK_VERSIONS, ReplayFilter, ReplayPolicy, build_manifest
from replay_execution import ReplayExecutionConfig, ReplayMarketRow, audit_chain_hash, execute_replay
from replay_hard_state import ReplayHardStateResult, ReplayTransitionElimination

UTC = timezone.utc
DAY = date(2026, 8, 21)
EVENT = "KXHIGHNY-26AUG21"
M1 = EVENT + "-B8687"
M2 = EVENT + "-B8485"


def now(offset: float = 0) -> datetime:
    base = datetime(2026, 8, 21, 18, 0, tzinfo=UTC)
    return datetime.fromtimestamp(base.timestamp() + offset, tz=UTC)


def snap(row_id: int, market: str, price: str, qty: str, connection: str, sid: int, seq: int, prev: str | None, offset: float):
    raw = json.dumps({"msg": {
        "market_ticker": market,
        "yes_dollars_fp": [[price, qty]],
        "no_dollars_fp": [[str(Decimal("1") - Decimal(price)), qty]],
    }}, separators=(",", ":"))
    digest = sha256(raw.encode()).hexdigest()
    receipt = now(offset)
    ns = int(receipt.timestamp() * 1_000_000_000)
    chain = audit_chain_hash(prev, connection, ns, digest)
    return ReplayMarketRow(
        row_id=row_id, channel="orderbook_snapshot", sid=sid, seq=seq,
        market_ticker=market, received_at=receipt, received_epoch_ms=ns // 1_000_000,
        received_epoch_ns=ns, raw_text=raw, payload_sha256=digest,
        connection_id=connection, prev_chain_hash=prev, chain_hash=chain,
    )


class PortfolioPlacementRegression(unittest.TestCase):
    def test_sequential_best_edge_first_rechecks_remaining_cash_before_each_trade(self) -> None:
        manifest = build_manifest(
            source_session_id="source",
            versions=CURRENT_BENCHMARK_VERSIONS,
            policy=ReplayPolicy.BENCHMARK,
            replay_filter=ReplayFilter(station_code="KNYC", event_ticker=EVENT, climate_date=DAY),
            events=(),
        )
        eliminations = []
        for i, market in enumerate((M1, M2)):
            eliminations.append({
                "elimination_id": f"e{i}", "event_ticker": EVENT,
                "market_ticker": market, "station_code": "KNYC",
                "climate_date": DAY.isoformat(), "hard_state_id": "state",
                "hard_lower_bound_f": 88, "strike_rule": "cap_strike=87",
                "eliminated": True, "elimination_model_version": "bucket-elimination-v1",
                "reason": "hard_lower_bound_strictly_above_market_cap",
            })
        payload = {
            "event_ticker": EVENT, "station_code": "KNYC", "climate_date": DAY.isoformat(),
            "hard_state_id": "state", "transition_evidence_id": "evidence",
            "event_rules_hash": "a" * 64, "accepted": True, "fail_closed_reason": None,
            "elimination_model_version": "bucket-elimination-v1",
            "eliminations": eliminations, "dead_market_tickers": [M1, M2],
        }
        timeline = HardStateTimeline("KNYC", DAY, "lst-climate-calendar-v1", (), ())
        hard = ReplayHardStateResult(
            manifest.manifest_id, "KNYC", DAY, (), timeline,
            (ReplayTransitionElimination("state", now(), 1, "a" * 64, True, None, (M1, M2), payload),),
            0, 0,
        )
        connection = "22222222-2222-2222-2222-222222222222"
        r1 = snap(1, M1, "0.10", "200", connection, 1, 1, None, .01)
        r2 = snap(2, M2, "0.10", "200", connection, 2, 1, r1.chain_hash, .02)
        config = ReplayExecutionConfig(
            starting_bankroll=Decimal("100"), starting_cash=Decimal("100"),
            execution_latency_ms=100, fee_multiplier=Decimal("1"),
            max_trade_pct=Decimal("1"), max_event_pct=Decimal("1"),
            max_region_pct=Decimal("1"), max_daily_deployed_pct=Decimal("1"),
            max_no_price=Decimal("1"), allocation="best_edge_first",
        )
        result = execute_replay(manifest=manifest, hard_state=hard, market_rows=[r1, r2], config=config)
        trades = [decision for decision in result.decisions if decision.decision == "trade"]
        self.assertGreaterEqual(len(trades), 1)
        self.assertGreaterEqual(result.ending_cash, Decimal("0"))
        self.assertLessEqual(sum((trade.total_cost for trade in trades), Decimal("0")), Decimal("100"))
        self.assertEqual(result.ending_cash, Decimal("100") - sum((trade.total_cost for trade in trades), Decimal("0")))


if __name__ == "__main__":
    unittest.main()
