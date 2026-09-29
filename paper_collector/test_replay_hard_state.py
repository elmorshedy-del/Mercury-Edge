from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256

from replay_domain import CURRENT_BENCHMARK_VERSIONS, ReplayFilter, ReplayPolicy, build_manifest
from replay_hard_state import (
    ReplayRawCapture,
    ReplayRuleSnapshot,
    ReplayWeatherIdentity,
    UnsupportedReplayVersion,
    reconstruct_from_inputs,
)

UTC = timezone.utc
DAY = date(2026, 8, 21)
STATION = "KNYC"
EVENT = "KXHIGHNY-26AUG21"


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 21, hour, minute, tzinfo=UTC)


def awc_report(raw: str, obs: datetime) -> dict:
    return {
        "icaoId": STATION,
        "obsTime": int(obs.timestamp()),
        "rawOb": raw,
        "metarType": "METAR",
    }


def capture(raw_id: int, received: datetime, report: dict) -> ReplayRawCapture:
    body = json.dumps([report], separators=(",", ":")).encode()
    return ReplayRawCapture(
        raw_source_id=raw_id,
        raw_bytes=body,
        received_at=received,
        received_epoch_ns=int(received.timestamp() * 1_000_000_000),
        payload_sha256=sha256(body).hexdigest(),
    )


def identity(weather_id: int, cap: ReplayRawCapture, report: dict) -> ReplayWeatherIdentity:
    observed = datetime.fromtimestamp(report["obsTime"], tz=UTC)
    return ReplayWeatherIdentity(
        weather_id=weather_id,
        raw_source_id=cap.raw_source_id,
        station_code=STATION,
        source="NOAA_AWC",
        report_type="METAR",
        observed_at=observed,
        first_seen_at=cap.received_at,
        received_epoch_ms=cap.received_epoch_ns // 1_000_000,
        raw_text=report["rawOb"],
    )


def markets() -> list[dict]:
    return [
        {"ticker": EVENT + "-T83", "floor_strike": None, "cap_strike": 83, "strike_type": "less", "open_time": dt(0).isoformat(), "close_time": dt(23).isoformat()},
        {"ticker": EVENT + "-B8485", "floor_strike": 84, "cap_strike": 85, "strike_type": "between", "open_time": dt(0).isoformat(), "close_time": dt(23).isoformat()},
        {"ticker": EVENT + "-B8687", "floor_strike": 86, "cap_strike": 87, "strike_type": "between", "open_time": dt(0).isoformat(), "close_time": dt(23).isoformat()},
        {"ticker": EVENT + "-B8889", "floor_strike": 88, "cap_strike": 89, "strike_type": "between", "open_time": dt(0).isoformat(), "close_time": dt(23).isoformat()},
        {"ticker": EVENT + "-T90", "floor_strike": 90, "cap_strike": None, "strike_type": "greater", "open_time": dt(0).isoformat(), "close_time": dt(23).isoformat()},
    ]


def rule(snapshot_id: int, captured_at: datetime, rules_hash: str, event: str = EVENT, market_rows: list[dict] | None = None) -> ReplayRuleSnapshot:
    return ReplayRuleSnapshot(
        snapshot_id=snapshot_id,
        event_ticker=event,
        captured_at=captured_at,
        rules_hash=rules_hash,
        raw_payload={"event": {"event_ticker": event, "markets": market_rows or markets()}},
    )


def manifest(event: str = EVENT, versions=CURRENT_BENCHMARK_VERSIONS):
    return build_manifest(
        source_session_id="source-session",
        versions=versions,
        policy=ReplayPolicy.BENCHMARK,
        replay_filter=ReplayFilter(station_code=STATION, event_ticker=event, climate_date=DAY),
        events=(),
    )


class HardStateReplayTests(unittest.TestCase):
    def fixture(self):
        r1 = awc_report("KNYC 211751Z 18005KT 10SM CLR 31/20 A3000 RMK AO2 T03060200", dt(17, 51))
        r2 = awc_report("KNYC 211851Z 18005KT 10SM CLR 31/20 A3000 RMK AO2 T03060200 10311", dt(18, 51))
        r3 = awc_report("KNYC 211951Z 18005KT 10SM CLR 30/20 A3000 RMK AO2 T03000200", dt(19, 51))
        c1 = capture(1, dt(18, 0), r1)
        c2 = capture(2, dt(19, 0), r2)
        c3 = capture(3, dt(20, 0), r3)
        ids = [identity(101, c1, r1), identity(102, c2, r2), identity(103, c3, r3)]
        return [c1, c2, c3], ids

    def test_known_asos_stream_is_deterministic_and_monotonic(self) -> None:
        captures, identities = self.fixture()
        old = rule(10, dt(17), "a" * 64)
        result_a = reconstruct_from_inputs(
            manifest=manifest(), captures=captures, identities=identities,
            rule_snapshots=[old], timezone_name="America/New_York",
        )
        result_b = reconstruct_from_inputs(
            manifest=manifest(), captures=list(reversed(captures)), identities=list(reversed(identities)),
            rule_snapshots=[old], timezone_name="America/New_York",
        )
        self.assertEqual([s.proven_daily_high_min_f for s in result_a.timeline.states], [87, 88])
        self.assertEqual(result_a.state_ids, result_b.state_ids)
        self.assertEqual(result_a.output_sha256, result_b.output_sha256)
        self.assertEqual(result_a.timeline.current_bound_f, 88)

    def test_same_receipt_current_and_hidden_six_hour_max_is_atomic_strongest_transition(self) -> None:
        captures, identities = self.fixture()
        result = reconstruct_from_inputs(
            manifest=manifest(), captures=[captures[1]], identities=[identities[1]],
            rule_snapshots=[rule(10, dt(17), "a" * 64)], timezone_name="America/New_York",
        )
        self.assertEqual(len(result.timeline.states), 1)
        self.assertEqual(result.timeline.states[0].proven_daily_high_min_f, 88)
        self.assertEqual(sum(1 for a in result.timeline.applications if a.status.value == "transition"), 1)

    def test_later_lower_temperature_does_not_reduce_state(self) -> None:
        captures, identities = self.fixture()
        result = reconstruct_from_inputs(
            manifest=manifest(), captures=captures, identities=identities,
            rule_snapshots=[rule(10, dt(17), "a" * 64)], timezone_name="America/New_York",
        )
        self.assertEqual(result.timeline.current_bound_f, 88)
        self.assertEqual(len(result.timeline.states), 2)

    def test_future_rule_snapshot_cannot_be_selected_early(self) -> None:
        captures, identities = self.fixture()
        old = rule(10, dt(17), "a" * 64)
        future_markets = markets()
        future_markets[2] = {**future_markets[2], "cap_strike": 100}
        future = rule(11, dt(19, 30), "b" * 64, market_rows=future_markets)
        result = reconstruct_from_inputs(
            manifest=manifest(), captures=captures[:2], identities=identities[:2],
            rule_snapshots=[future, old], timezone_name="America/New_York",
        )
        elimination = result.eliminations[-1]
        self.assertEqual(elimination.rule_snapshot_id, 10)
        self.assertEqual(elimination.rule_rules_hash, "a" * 64)
        self.assertIn(EVENT + "-B8687", elimination.dead_market_tickers)

    def test_no_causal_rule_snapshot_means_no_authoritative_elimination(self) -> None:
        captures, identities = self.fixture()
        result = reconstruct_from_inputs(
            manifest=manifest(), captures=captures[:1], identities=identities[:1],
            rule_snapshots=[rule(20, dt(22), "c" * 64)], timezone_name="America/New_York",
        )
        self.assertEqual(result.eliminations[0].fail_closed_reason, "no_causal_rule_snapshot")
        self.assertFalse(result.eliminations[0].accepted)

    def test_wrong_date_event_fails_closed(self) -> None:
        captures, identities = self.fixture()
        wrong_event = "KXHIGHNY-26AUG19"
        wrong_rule = rule(10, dt(17), "d" * 64, event=wrong_event)
        result = reconstruct_from_inputs(
            manifest=manifest(wrong_event), captures=captures[:2], identities=identities[:2],
            rule_snapshots=[wrong_rule], timezone_name="America/New_York",
        )
        self.assertEqual(result.eliminations[-1].fail_closed_reason, "event_climate_date_mismatch")
        self.assertFalse(result.eliminations[-1].accepted)

    def test_v1_identity_mismatch_fails_closed(self) -> None:
        captures, identities = self.fixture()
        bad = replace(identities[0], raw_source_id=999)
        with self.assertRaisesRegex(ValueError, "v1 replay identity mismatch"):
            reconstruct_from_inputs(
                manifest=manifest(), captures=[captures[0]], identities=[bad],
                rule_snapshots=[], timezone_name="America/New_York",
            )

    def test_unsupported_parser_version_is_explicit(self) -> None:
        captures, identities = self.fixture()
        versions = replace(CURRENT_BENCHMARK_VERSIONS, parser_version="unknown-parser")
        with self.assertRaisesRegex(UnsupportedReplayVersion, "UNSUPPORTED_VERSION"):
            reconstruct_from_inputs(
                manifest=manifest(versions=versions), captures=[captures[0]], identities=[identities[0]],
                rule_snapshots=[], timezone_name="America/New_York",
            )


if __name__ == "__main__":
    unittest.main()
