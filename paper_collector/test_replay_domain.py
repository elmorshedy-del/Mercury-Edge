from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from replay_domain import (
    CURRENT_BENCHMARK_VERSIONS,
    ReplayEvent,
    ReplayEventKind,
    ReplayFilter,
    ReplayPolicy,
    ReplayVersionBundle,
    benchmark_events,
    build_manifest,
    sort_replay_events,
    source_input_hash,
)

UTC = timezone.utc


def ts(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 8, 21, hour, minute, second, tzinfo=UTC)


def ev(
    kind: ReplayEventKind,
    source_id: str,
    when: datetime,
    *,
    epoch_ns: int | None = None,
    sequence_key: str | None = None,
    source: str = "TEST",
    stream: str | None = None,
    live_causal: bool = True,
    benchmark_admissible: bool = True,
    metadata: dict | None = None,
) -> ReplayEvent:
    return ReplayEvent(
        kind=kind,
        source_id=source_id,
        available_at=when,
        available_epoch_ns=epoch_ns,
        payload_sha256=(source_id.encode().hex() + "0" * 64)[:64],
        source=source,
        source_stream=stream,
        sequence_key=sequence_key,
        live_causal=live_causal,
        benchmark_admissible=benchmark_admissible,
        metadata=metadata or {},
    )


class ReplayEventOrderingTests(unittest.TestCase):
    def test_events_sort_deterministically_independent_of_input_order(self) -> None:
        events = [
            ev(ReplayEventKind.MARKET_MESSAGE, "m2", ts(15), epoch_ns=200, sequence_key="b"),
            ev(ReplayEventKind.RAW_SOURCE, "w1", ts(14), epoch_ns=100),
            ev(ReplayEventKind.MARKET_MESSAGE, "m1", ts(15), epoch_ns=200, sequence_key="a"),
            ev(ReplayEventKind.RULE_SNAPSHOT, "r1", ts(15), epoch_ns=200),
        ]
        expected = ["w1", "r1", "m1", "m2"]
        self.assertEqual([x.source_id for x in sort_replay_events(events)], expected)
        self.assertEqual([x.source_id for x in sort_replay_events(reversed(events))], expected)

    def test_observed_time_is_provenance_only_not_sort_time(self) -> None:
        physically_old_received_late = ev(
            ReplayEventKind.RAW_SOURCE,
            "late",
            ts(18),
            epoch_ns=180,
            metadata={"observed_at": ts(10).isoformat()},
        )
        normal = ev(
            ReplayEventKind.RAW_SOURCE,
            "normal",
            ts(12),
            epoch_ns=120,
            metadata={"observed_at": ts(12).isoformat()},
        )
        self.assertEqual(
            [x.source_id for x in sort_replay_events([physically_old_received_late, normal])],
            ["normal", "late"],
        )

    def test_rule_snapshot_availability_is_capture_time(self) -> None:
        rule = ev(ReplayEventKind.RULE_SNAPSHOT, "rule", ts(16))
        weather = ev(ReplayEventKind.RAW_SOURCE, "weather", ts(15))
        self.assertEqual([x.source_id for x in sort_replay_events([rule, weather])], ["weather", "rule"])

    def test_market_tie_break_uses_sequence_then_stable_id(self) -> None:
        a = ev(ReplayEventKind.MARKET_MESSAGE, "row:2", ts(15), epoch_ns=1000, sequence_key="c:1:7:2")
        b = ev(ReplayEventKind.MARKET_MESSAGE, "row:1", ts(15), epoch_ns=1000, sequence_key="c:1:6:1")
        self.assertEqual([x.source_id for x in sort_replay_events([a, b])], ["row:1", "row:2"])


class ArchiveCausalityTests(unittest.TestCase):
    def test_archive_import_received_later_remains_later_and_benchmark_inadmissible(self) -> None:
        live = ev(
            ReplayEventKind.RAW_SOURCE,
            "live",
            ts(15),
            epoch_ns=150,
            source="MADIS_OMO",
            stream="madis_omo_ldm",
        )
        archive = ev(
            ReplayEventKind.RAW_SOURCE,
            "archive",
            ts(20),
            epoch_ns=200,
            source="MADIS_OMO",
            stream="madis_omo_archive",
            live_causal=False,
            benchmark_admissible=False,
            metadata={"observed_at": ts(11).isoformat(), "transport": "archive_import"},
        )
        self.assertEqual([x.source_id for x in sort_replay_events([archive, live])], ["live", "archive"])
        self.assertEqual([x.source_id for x in benchmark_events([archive, live])], ["live"])
        self.assertTrue(live.live_causal)
        self.assertFalse(archive.live_causal)


class ManifestIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = (
            ev(ReplayEventKind.RAW_SOURCE, "weather", ts(15), epoch_ns=150),
            ev(ReplayEventKind.RULE_SNAPSHOT, "rule", ts(15, 1)),
            ev(ReplayEventKind.MARKET_MESSAGE, "market", ts(15, 2), epoch_ns=152),
        )
        self.filter = ReplayFilter(station_code="KNYC", event_ticker="KXHIGHNY-26AUG21", climate_date=date(2026, 8, 21))

    def test_same_inputs_versions_filter_produce_identical_manifest(self) -> None:
        a = build_manifest(
            source_session_id="session-a",
            versions=CURRENT_BENCHMARK_VERSIONS,
            policy=ReplayPolicy.BENCHMARK,
            replay_filter=self.filter,
            events=self.events,
        )
        b = build_manifest(
            source_session_id="session-a",
            versions=CURRENT_BENCHMARK_VERSIONS,
            policy=ReplayPolicy.BENCHMARK,
            replay_filter=self.filter,
            events=tuple(reversed(self.events)),
        )
        self.assertEqual(a.source_input_sha256, b.source_input_sha256)
        self.assertEqual(a.manifest_id, b.manifest_id)
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_component_version_changes_manifest_not_source_input_hash(self) -> None:
        base = build_manifest(
            source_session_id="session-a",
            versions=CURRENT_BENCHMARK_VERSIONS,
            policy=ReplayPolicy.BENCHMARK,
            replay_filter=self.filter,
            events=self.events,
        )
        altered_versions = replace(CURRENT_BENCHMARK_VERSIONS, execution_version="execution-v2")
        altered = build_manifest(
            source_session_id="session-a",
            versions=altered_versions,
            policy=ReplayPolicy.BENCHMARK,
            replay_filter=self.filter,
            events=self.events,
        )
        self.assertEqual(base.source_input_sha256, altered.source_input_sha256)
        self.assertNotEqual(base.manifest_id, altered.manifest_id)

    def test_source_input_hash_is_order_independent(self) -> None:
        self.assertEqual(source_input_hash(self.events), source_input_hash(tuple(reversed(self.events))))

    def test_all_component_versions_are_required(self) -> None:
        with self.assertRaises(ValueError):
            ReplayVersionBundle(
                parser_version="",
                calendar_version="c",
                evidence_model_version="e",
                hard_state_version="h",
                elimination_version="b",
                execution_version="x",
            )


if __name__ == "__main__":
    unittest.main()
