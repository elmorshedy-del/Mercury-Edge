from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://example.invalid/mercury_test")
os.environ.setdefault("PAPER_SESSION_ID", "test-hard-state-session")

import hard_state_proof as hp
import paper_engine_hardened as dbn
import strategy_runtime_hardened as runtime

UTC = timezone.utc


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def execute(self, query, params=None):
        self.calls.append((str(query), params))
        return FakeResult()


def make_proof(
    bound: int,
    *,
    weather_id: int = 7,
    raw_group: str = "T03110200",
    kind: str = "t_group",
    grade: str = hp.H1_CURRENT,
    at: datetime | None = None,
) -> hp.HardStateProof:
    at = at or datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
    return hp.HardStateProof(
        climate_trade_date=date(2026, 8, 18),
        proven_daily_high_min_f=bound,
        trigger_weather_id=weather_id,
        trigger_at=at,
        trigger_epoch_ms=int(at.timestamp() * 1000),
        trigger_kind=kind,
        trigger_raw_group=raw_group,
        trigger_grade=grade,
        source_weather_ids_at_bound=(weather_id,),
        supporting_records=(),
        rejected_row_count=0,
    )


def weather(
    *,
    weather_id: int = 7,
    station: str = "KPHL",
    observed: datetime | None = None,
    seen: datetime | None = None,
    decoded_temp: Decimal = Decimal("87.8"),
) -> dict:
    observed = observed or datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
    seen = seen or datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
    return {
        "id": weather_id,
        "station_code": station,
        "source": "NOAA_AWC",
        "report_type": "METAR",
        "observed_at": observed,
        "first_seen_at": seen,
        "received_epoch_ms": int(seen.timestamp() * 1000),
        "received_epoch_ns": int(seen.timestamp() * 1_000_000_000),
        "temperature_f": decoded_temp,
        "max_temperature_f": decoded_temp,
        "compatibility_status": "unverified",
        "compatibility_rule": None,
    }


def event(event_ticker: str = "KXHIGHPHIL-26AUG18", cap: int = 87) -> dict:
    return {
        "event_ticker": event_ticker,
        "rules_hash": "event-rules",
        "mutually_exclusive": True,
        "markets": [
            {"ticker": f"TEST-T{cap + 1}", "floor_strike": None, "cap_strike": cap},
            {"ticker": "TEST-UPPER", "floor_strike": cap + 1, "cap_strike": None},
        ],
    }


class DbnProofIntegrationTests(unittest.TestCase):
    def common_patches(self, proof, events):
        return (
            patch.object(dbn.hard_state_proof, "proof_for_weather", return_value=proof),
            patch.object(dbn.base, "dbn_strategy", return_value={"paper_trade_enabled": True, "shadow_enabled": True, "config": {}}),
            patch.object(dbn.base, "series_rules_before", return_value={"rules_hash": "series-rules", "fee_type": "quadratic", "fee_multiplier": Decimal("1")}),
            patch.object(dbn.base, "event_rule_candidates", return_value=events),
            patch.object(dbn.base, "load_global", return_value={}),
            patch.object(dbn.base, "load_modes", return_value=[]),
        )

    def test_decoded_87_8_cannot_kill_upper_87_when_raw_proof_is_only_87(self) -> None:
        conn = FakeConnection()
        proof = make_proof(87)
        patches = self.common_patches(proof, [event(cap=87)])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(dbn.base, "insert_signal") as insert_signal, \
             patch.object(dbn.base, "execute_candidates") as execute:
            count = dbn.process_weather(conn, weather(decoded_temp=Decimal("87.8")))
        self.assertEqual(count, 0)
        insert_signal.assert_not_called()
        execute.assert_not_called()

    def test_t0311_style_proof_kills_upper_87_and_candidate_uses_88(self) -> None:
        conn = FakeConnection()
        proof = make_proof(88)
        patches = self.common_patches(proof, [event(cap=87)])
        captured = []
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(dbn.base, "insert_signal", return_value=(41, "approved")) as insert_signal, \
             patch.object(dbn.base, "execute_candidates", side_effect=lambda _c, candidates, _m, _g: captured.extend(candidates)):
            count = dbn.process_weather(conn, weather(decoded_temp=Decimal("87.0")))
        self.assertEqual(count, 1)
        self.assertEqual(insert_signal.call_args.kwargs["confirmed_high"], Decimal("88"))
        self.assertTrue(insert_signal.call_args.kwargs["proven"])
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].confirmed_high_f, Decimal("88"))
        self.assertEqual(captured[0].evidence["hard_state_proof"]["trigger_raw_group"], "T03110200")

    def test_repeated_same_bound_does_not_retrigger_dbn(self) -> None:
        conn = FakeConnection()
        current = weather(weather_id=8)
        proof = make_proof(88, weather_id=7)
        with patch.object(dbn.hard_state_proof, "proof_for_weather", return_value=proof), \
             patch.object(dbn.base, "dbn_strategy", return_value={"paper_trade_enabled": True}), \
             patch.object(dbn.base, "insert_signal") as insert_signal:
            self.assertEqual(dbn.process_weather(conn, current), 0)
        insert_signal.assert_not_called()

    def test_off_lattice_or_missing_raw_proof_cannot_create_dbn(self) -> None:
        conn = FakeConnection()
        with patch.object(dbn.hard_state_proof, "proof_for_weather", return_value=None), \
             patch.object(dbn.base, "dbn_strategy", return_value={"paper_trade_enabled": True}), \
             patch.object(dbn.base, "insert_signal") as insert_signal:
            self.assertEqual(dbn.process_weather(conn, weather()), 0)
        insert_signal.assert_not_called()

    def test_dst_boundary_routes_only_to_matching_climate_date_event(self) -> None:
        conn = FakeConnection()
        observed = datetime(2026, 8, 19, 4, 30, tzinfo=UTC)  # 00:30 EDT => Aug 18 climate date
        seen = datetime(2026, 8, 19, 4, 31, tzinfo=UTC)
        w = weather(observed=observed, seen=seen)
        proof = make_proof(88, at=seen)
        events = [event("KXHIGHPHIL-26AUG18", 87), event("KXHIGHPHIL-26AUG19", 87)]
        patches = self.common_patches(proof, events)
        tickers = []
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(dbn.base, "insert_signal", side_effect=lambda _c, **kwargs: (tickers.append(kwargs["event_ticker"]) or 1, "shadow_only")):
            count = dbn.process_weather(conn, w)
        self.assertEqual(count, 0)  # shadow-only signals are not executable candidates
        self.assertEqual(tickers, ["KXHIGHPHIL-26AUG18"])

    def test_signal_update_contains_raw_provenance(self) -> None:
        conn = FakeConnection()
        proof = make_proof(88)
        dbn._attach_proof_to_signal(conn, 99, proof)
        self.assertEqual(len(conn.calls), 1)
        params = conn.calls[0][1]
        payload = json.loads(params[0])
        self.assertEqual(payload["trigger_raw_group"], "T03110200")
        self.assertEqual(payload["proven_daily_high_min_f"], 88)
        self.assertEqual(params[-1], 99)


class HardCoreRuntimeTests(unittest.TestCase):
    def test_hard_transition_weather_overrides_decoded_temperature_with_proof_bound(self) -> None:
        proof = make_proof(77, raw_group="10250", kind="six_hour_max", grade=hp.H2_SIX_HOUR_MAX)
        w = weather(station="KLAX", decoded_temp=Decimal("74"))
        with patch.object(runtime.hard_state_proof, "proof_for_weather", return_value=proof):
            returned, guarded = runtime._hard_transition_weather(FakeConnection(), "s", w, "America/Los_Angeles")
        self.assertIs(returned, proof)
        self.assertEqual(guarded["max_temperature_f"], Decimal("77"))
        self.assertEqual(guarded["received_epoch_ms"], proof.trigger_epoch_ms)

    def test_hsr_bundle_is_constructed_from_proof_and_carries_provenance(self) -> None:
        proof = make_proof(88)
        hard_weather = weather(decoded_temp=Decimal("75"))
        hard_weather["max_temperature_f"] = Decimal("88")
        configs = {
            "HSR": {
                "enabled": True,
                "paper_trade_enabled": True,
                "shadow_enabled": True,
                "config": {"confidence_gate": "shadow_only", "max_entry_price": 0.97},
            }
        }
        inserted = []
        with patch.object(runtime, "_hard_transition_weather", return_value=(proof, hard_weather)), \
             patch.object(runtime, "confirmed_same_day_high", return_value=None), \
             patch.object(runtime.se, "strategy_configs", return_value=configs), \
             patch.object(runtime.se, "series_rules_before", return_value={"fee_type": "quadratic", "fee_multiplier": Decimal("1")}), \
             patch.object(runtime.se, "compatibility_proven", return_value=False), \
             patch.object(runtime.se, "event_rules_before", return_value=[event(cap=87)]), \
             patch.object(runtime.se, "insert_signal", side_effect=lambda _c, _s, bundle: (inserted.append(bundle) or 1)), \
             patch.object(runtime.shadow_lab, "record_bundle_trials", return_value=0), \
             patch.object(runtime.legacy, "execute_with_strategy_guard") as execute:
            count = runtime.execute_extra_strategies(FakeConnection(), "s", weather(), [], {})
        self.assertEqual(count, 1)
        self.assertEqual(len(inserted), 1)
        bundle = inserted[0]
        self.assertEqual(bundle.strategy_code, "HSR")
        self.assertEqual(bundle.evidence["confirmed_high_f"], Decimal("88"))
        self.assertEqual(bundle.evidence["hard_state_proof"]["trigger_raw_group"], "T03110200")
        self.assertEqual(bundle.evidence["proven_daily_high_min_f"], 88)
        execute.assert_not_called()  # shadow-only must never spend benchmark cash

    def test_hidden_six_hour_max_can_kill_bucket_while_current_print_is_lower(self) -> None:
        proof = make_proof(77, raw_group="10250", kind="six_hour_max", grade=hp.H2_SIX_HOUR_MAX)
        hard_weather = weather(station="KLAX", decoded_temp=Decimal("74"))
        hard_weather["max_temperature_f"] = Decimal("77")
        configs = {
            "HSR": {"enabled": True, "paper_trade_enabled": False, "shadow_enabled": False, "config": {"confidence_gate": "shadow_only"}}
        }
        la_event = {
            "event_ticker": "KXHIGHLAX-26AUG18",
            "rules_hash": "r",
            "mutually_exclusive": True,
            "markets": [
                {"ticker": "LAX-76", "floor_strike": None, "cap_strike": 76},
                {"ticker": "LAX-77UP", "floor_strike": 77, "cap_strike": None},
            ],
        }
        inserted = []
        with patch.object(runtime, "_hard_transition_weather", return_value=(proof, hard_weather)), \
             patch.object(runtime, "confirmed_same_day_high", return_value=None), \
             patch.object(runtime.se, "strategy_configs", return_value=configs), \
             patch.object(runtime.se, "series_rules_before", return_value={"fee_type": "quadratic", "fee_multiplier": Decimal("1")}), \
             patch.object(runtime.se, "compatibility_proven", return_value=False), \
             patch.object(runtime.se, "event_rules_before", return_value=[la_event]), \
             patch.object(runtime.se, "insert_signal", side_effect=lambda _c, _s, bundle: (inserted.append(bundle) or 1)):
            count = runtime.execute_extra_strategies(FakeConnection(), "s", weather(station="KLAX"), [], {})
        self.assertEqual(count, 1)
        self.assertEqual(inserted[0].evidence["dead_markets"], ["LAX-76"])
        self.assertEqual(inserted[0].evidence["trigger_evidence_grade"], hp.H2_SIX_HOUR_MAX)

    def test_no_hard_proof_means_no_hard_core_bundle(self) -> None:
        with patch.object(runtime, "_hard_transition_weather", return_value=(None, None)), \
             patch.object(runtime, "confirmed_same_day_high", return_value=None), \
             patch.object(runtime.se, "insert_signal") as insert_signal:
            self.assertEqual(runtime.execute_extra_strategies(FakeConnection(), "s", weather(), [], {}), 0)
        insert_signal.assert_not_called()

    def test_benchmark_gate_blocks_shadow_only_even_if_paper_enabled(self) -> None:
        bundle = runtime.se.Bundle(
            strategy_code="HSR",
            signal_class="hard_state",
            station="KPHL",
            region="northeast",
            event_ticker="E",
            weather_id=1,
            trigger_at=datetime(2026, 8, 18, tzinfo=UTC),
            trigger_epoch_ms=1,
            legs=[],
            evidence={},
            auditor_status="approved",
            blocked_reason=None,
        )
        shadow_cfg = {"paper_trade_enabled": True, "config": {"confidence_gate": "shadow_only"}}
        approved_cfg = {"paper_trade_enabled": True, "config": {"confidence_gate": "approved_only"}}
        self.assertFalse(runtime._benchmark_eligible(bundle, shadow_cfg))
        self.assertTrue(runtime._benchmark_eligible(bundle, approved_cfg))
        bundle.auditor_status = "shadow_only"
        self.assertFalse(runtime._benchmark_eligible(bundle, approved_cfg))


if __name__ == "__main__":
    unittest.main()
