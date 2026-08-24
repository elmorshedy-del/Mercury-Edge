from __future__ import annotations

"""Step 4J-B: reconstruct canonical hard state and eliminations from source bytes.

Semantic weather input is the immutable raw AWC payload. Historical
``live_weather_journal`` rows are used only to recover the v1 surrogate
``weather_id`` required by the existing evidence-id algorithm; decoded weather
columns are never read.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping, Sequence

import psycopg

import bucket_elimination
import hard_state_proof as current_proof
from asos_evidence import parse_temperature_evidence
from hard_information_domain import HardClimateState, SettlementEvidence
from hard_state_accumulator import HARD_STATE_ACCUMULATOR_VERSION, HardStateTimeline, accumulate_hard_state
from market_calendar import CLIMATE_CALENDAR_VERSION, climate_date, six_hour_window_within_climate_day
from raw_journal import canonical_json_bytes
from replay_domain import ReplayEvent, ReplayEventKind, ReplayManifest, ReplayPolicy, ReplayVersionBundle
from stations import STATIONS

REPLAY_HARD_STATE_VERSION = "replay-hard-state-v1"


class UnsupportedReplayVersion(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplayRawCapture:
    raw_source_id: int
    raw_bytes: bytes
    received_at: datetime
    received_epoch_ns: int
    payload_sha256: str

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is None:
            raise ValueError("capture receipt must be timezone-aware")
        if sha256(self.raw_bytes).hexdigest() != self.payload_sha256:
            raise ValueError("immutable raw capture hash mismatch")


@dataclass(frozen=True)
class ReplayWeatherIdentity:
    weather_id: int
    raw_source_id: int
    station_code: str
    source: str
    report_type: str
    observed_at: datetime
    first_seen_at: datetime
    received_epoch_ms: int
    raw_text: str


@dataclass(frozen=True)
class ReplayRuleSnapshot:
    snapshot_id: int
    event_ticker: str
    captured_at: datetime
    rules_hash: str
    raw_payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ValueError("rule capture time must be timezone-aware")


@dataclass(frozen=True)
class ReplayTransitionElimination:
    state_id: str
    known_at: datetime
    rule_snapshot_id: int | None
    rule_rules_hash: str | None
    accepted: bool
    fail_closed_reason: str | None
    dead_market_tickers: tuple[str, ...]
    elimination_payload: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "known_at": self.known_at.isoformat(),
            "rule_snapshot_id": self.rule_snapshot_id,
            "rule_rules_hash": self.rule_rules_hash,
            "accepted": self.accepted,
            "fail_closed_reason": self.fail_closed_reason,
            "dead_market_tickers": list(self.dead_market_tickers),
            "elimination_payload": dict(self.elimination_payload) if self.elimination_payload else None,
        }


@dataclass(frozen=True)
class ReplayHardStateResult:
    manifest_id: str
    station_code: str
    climate_date: date
    evidence: tuple[SettlementEvidence, ...]
    timeline: HardStateTimeline
    eliminations: tuple[ReplayTransitionElimination, ...]
    rejected_report_count: int
    ignored_duplicate_report_count: int
    replay_model_version: str = REPLAY_HARD_STATE_VERSION

    @property
    def state_ids(self) -> tuple[str, ...]:
        return tuple(state.state_id for state in self.timeline.states)

    @property
    def output_sha256(self) -> str:
        return sha256(canonical_json_bytes(self.to_dict(include_hash=False))).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {
            "manifest_id": self.manifest_id,
            "station_code": self.station_code,
            "climate_date": self.climate_date.isoformat(),
            "evidence": [item.to_dict() for item in self.evidence],
            "timeline": self.timeline.to_dict(),
            "eliminations": [item.to_dict() for item in self.eliminations],
            "rejected_report_count": self.rejected_report_count,
            "ignored_duplicate_report_count": self.ignored_duplicate_report_count,
            "replay_model_version": self.replay_model_version,
        }
        if include_hash:
            payload["output_sha256"] = self.output_sha256
        return payload


def assert_supported_versions(versions: ReplayVersionBundle) -> None:
    expected = {
        "parser_version": current_proof.PARSER_VERSION,
        "calendar_version": CLIMATE_CALENDAR_VERSION,
        "evidence_model_version": current_proof.PROOF_VERSION,
        "hard_state_version": HARD_STATE_ACCUMULATOR_VERSION,
        "elimination_version": bucket_elimination.ELIMINATION_MODEL_VERSION,
    }
    actual = versions.to_dict()
    mismatches = [f"{key}={actual[key]!r}" for key, value in expected.items() if actual.get(key) != value]
    if mismatches:
        raise UnsupportedReplayVersion("UNSUPPORTED_VERSION: " + ", ".join(mismatches))


def parse_awc_captures(
    captures: Sequence[ReplayRawCapture],
    identities: Sequence[ReplayWeatherIdentity],
    *,
    station_code: str,
    target_climate_date: date,
    timezone_name: str,
) -> tuple[tuple[SettlementEvidence, ...], int, int]:
    """Reparse immutable AWC batches into current-version canonical evidence."""
    identity_by_capture: dict[int, list[ReplayWeatherIdentity]] = {}
    for identity in identities:
        identity_by_capture.setdefault(identity.raw_source_id, []).append(identity)

    evidence: list[SettlementEvidence] = []
    seen_reports: set[tuple[str, str, str]] = set()
    rejected = 0
    duplicates = 0

    for capture in sorted(captures, key=lambda item: (item.received_epoch_ns, item.raw_source_id)):
        try:
            decoded = json.loads(capture.raw_bytes)
        except Exception as exc:
            raise ValueError(f"AWC raw capture {capture.raw_source_id} is not valid JSON") from exc
        if not isinstance(decoded, list):
            raise ValueError(f"AWC raw capture {capture.raw_source_id} is not a JSON list")

        capture_identities = identity_by_capture.get(capture.raw_source_id, [])
        for report in decoded:
            if not isinstance(report, Mapping):
                rejected += 1
                continue
            station = str(report.get("icaoId") or "")
            raw_text = str(report.get("rawOb") or "")
            obs_time = report.get("obsTime")
            if station != station_code or not raw_text or obs_time is None:
                continue
            observed_at = datetime.fromtimestamp(float(obs_time), tz=timezone.utc)
            if climate_date(observed_at, timezone_name) != target_climate_date:
                continue

            report_key = (station, observed_at.isoformat(), raw_text)
            if report_key in seen_reports:
                duplicates += 1
                continue
            seen_reports.add(report_key)

            matches = [
                row for row in capture_identities
                if row.station_code == station
                and row.raw_text == raw_text
                and row.observed_at == observed_at
                and row.source == "NOAA_AWC"
                and row.received_epoch_ms == capture.received_epoch_ns // 1_000_000
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"v1 replay identity mismatch for raw_source_id={capture.raw_source_id} "
                    f"station={station} observed_at={observed_at.isoformat()} matches={len(matches)}"
                )
            identity = matches[0]

            items = parse_temperature_evidence(raw_text)
            if not items or not current_proof._row_evidence_is_coherent(items):
                rejected += 1
                continue

            for item in items:
                if not item.hard_state_eligible or item.proven_min_f is None or item.proven_max_f is None:
                    continue
                if item.kind == "twenty_four_hour_max":
                    continue
                if item.kind == "six_hour_max":
                    if not six_hour_window_within_climate_day(observed_at, timezone_name):
                        continue
                    grade = current_proof.H2_SIX_HOUR_MAX
                elif item.kind in ("main_temp_c", "t_group"):
                    grade = current_proof.H1_CURRENT
                else:
                    continue

                proof_record = current_proof.ProofRecord(
                    weather_id=identity.weather_id,
                    raw_source_id=capture.raw_source_id,
                    source="NOAA_AWC",
                    report_type=identity.report_type,
                    observed_at=observed_at,
                    first_seen_at=identity.first_seen_at,
                    received_epoch_ms=identity.received_epoch_ms,
                    kind=item.kind,
                    raw_group=item.raw_group,
                    encoded_c=item.encoded_c,
                    possible_canonical_f=item.possible_canonical_f,
                    proven_min_f=int(item.proven_min_f),
                    proven_max_f=int(item.proven_max_f),
                    grade=grade,
                )
                evidence.append(proof_record.to_settlement_evidence(station_code, target_climate_date))

    return tuple(evidence), rejected, duplicates


def select_rule_snapshot(
    snapshots: Sequence[ReplayRuleSnapshot],
    *,
    event_ticker: str,
    known_at: datetime,
) -> ReplayRuleSnapshot | None:
    eligible = [
        item for item in snapshots
        if item.event_ticker == event_ticker and item.captured_at <= known_at
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda item: (item.captured_at, item.snapshot_id))


def eliminate_timeline(
    timeline: HardStateTimeline,
    snapshots: Sequence[ReplayRuleSnapshot],
    *,
    event_ticker: str | None,
) -> tuple[ReplayTransitionElimination, ...]:
    out: list[ReplayTransitionElimination] = []
    for state in timeline.states:
        if not event_ticker:
            out.append(_no_elimination(state, "missing_event_filter"))
            continue
        snapshot = select_rule_snapshot(snapshots, event_ticker=event_ticker, known_at=state.first_known_at)
        if snapshot is None:
            out.append(_no_elimination(state, "no_causal_rule_snapshot"))
            continue
        event = _event_from_rule_snapshot(snapshot, state)
        if event is None:
            out.append(ReplayTransitionElimination(
                state_id=state.state_id,
                known_at=state.first_known_at,
                rule_snapshot_id=snapshot.snapshot_id,
                rule_rules_hash=snapshot.rules_hash,
                accepted=False,
                fail_closed_reason="invalid_or_no_active_event_markets",
                dead_market_tickers=(),
            ))
            continue
        result = bucket_elimination.evaluate_event(event, state)
        out.append(ReplayTransitionElimination(
            state_id=state.state_id,
            known_at=state.first_known_at,
            rule_snapshot_id=snapshot.snapshot_id,
            rule_rules_hash=snapshot.rules_hash,
            accepted=result.accepted,
            fail_closed_reason=result.fail_closed_reason,
            dead_market_tickers=result.dead_market_tickers,
            elimination_payload=result.to_dict(),
        ))
    return tuple(out)


def reconstruct_from_inputs(
    *,
    manifest: ReplayManifest,
    captures: Sequence[ReplayRawCapture],
    identities: Sequence[ReplayWeatherIdentity],
    rule_snapshots: Sequence[ReplayRuleSnapshot],
    timezone_name: str,
) -> ReplayHardStateResult:
    assert_supported_versions(manifest.versions)
    filt = manifest.replay_filter
    if not filt.station_code or not filt.climate_date:
        raise ValueError("4J-B replay requires explicit station_code and climate_date filters")
    if manifest.policy is not ReplayPolicy.BENCHMARK:
        # Current Step-4 benchmark hard-state implementation deliberately has no
        # research-source promotion path. Research A/B is added at registered
        # component-version boundaries, not by weakening the benchmark gate.
        pass

    evidence, rejected, duplicates = parse_awc_captures(
        captures,
        identities,
        station_code=filt.station_code,
        target_climate_date=filt.climate_date,
        timezone_name=timezone_name,
    )
    timeline = accumulate_hard_state(
        evidence,
        station_code=filt.station_code,
        climate_date=filt.climate_date,
        calendar_version=manifest.versions.calendar_version,
    )
    eliminations = eliminate_timeline(timeline, rule_snapshots, event_ticker=filt.event_ticker)
    return ReplayHardStateResult(
        manifest_id=manifest.manifest_id,
        station_code=filt.station_code,
        climate_date=filt.climate_date,
        evidence=evidence,
        timeline=timeline,
        eliminations=eliminations,
        rejected_report_count=rejected,
        ignored_duplicate_report_count=duplicates,
    )


def reconstruct_from_database(
    conn: psycopg.Connection[Any],
    *,
    manifest: ReplayManifest,
    events: Iterable[ReplayEvent],
) -> ReplayHardStateResult:
    """DB wrapper that reads source bytes/rule snapshots, never prior hard state."""
    assert_supported_versions(manifest.versions)
    filt = manifest.replay_filter
    if not filt.station_code or not filt.climate_date:
        raise ValueError("4J-B replay requires explicit station_code and climate_date filters")
    station_meta = STATIONS.get(filt.station_code)
    if not station_meta or not station_meta.get("timezone"):
        raise ValueError(f"unknown station/timezone: {filt.station_code}")

    admissible_raw_ids = []
    for event in events:
        if event.kind is ReplayEventKind.RAW_SOURCE and event.benchmark_admissible and event.source == "NOAA_AWC" and event.source_stream == "metar_json_batch":
            prefix = "raw_source_journal:"
            if not event.source_id.startswith(prefix):
                raise ValueError("unexpected raw source replay identity")
            admissible_raw_ids.append(int(event.source_id[len(prefix):]))

    captures: list[ReplayRawCapture] = []
    identities: list[ReplayWeatherIdentity] = []
    for raw_id in sorted(set(admissible_raw_ids)):
        row = conn.execute(
            """
            SELECT raw_bytes,received_at,received_epoch_ns,payload_sha256
              FROM raw_source_journal
             WHERE session_id=%s AND id=%s AND source='NOAA_AWC' AND source_stream='metar_json_batch'
            """,
            (manifest.source_session_id, raw_id),
        ).fetchone()
        if not row:
            raise ValueError(f"missing immutable AWC capture {raw_id}")
        raw_bytes = bytes(row[0])
        captures.append(ReplayRawCapture(
            raw_source_id=raw_id,
            raw_bytes=raw_bytes,
            received_at=_aware(row[1]),
            received_epoch_ns=int(row[2]),
            payload_sha256=str(row[3]),
        ))
        identity_rows = conn.execute(
            """
            SELECT id,raw_source_id,station_code,source,report_type,observed_at,first_seen_at,
                   received_epoch_ms,raw_text
              FROM live_weather_journal
             WHERE session_id=%s AND raw_source_id=%s AND source='NOAA_AWC'
            """,
            (manifest.source_session_id, raw_id),
        ).fetchall()
        for identity in identity_rows:
            identities.append(ReplayWeatherIdentity(
                weather_id=int(identity[0]),
                raw_source_id=int(identity[1]),
                station_code=str(identity[2]),
                source=str(identity[3]),
                report_type=str(identity[4]),
                observed_at=_aware(identity[5]),
                first_seen_at=_aware(identity[6]),
                received_epoch_ms=int(identity[7]),
                raw_text=str(identity[8]),
            ))

    rule_snapshots = _load_rule_snapshots(conn, manifest.source_session_id, filt.event_ticker)
    return reconstruct_from_inputs(
        manifest=manifest,
        captures=captures,
        identities=identities,
        rule_snapshots=rule_snapshots,
        timezone_name=str(station_meta["timezone"]),
    )


def _load_rule_snapshots(
    conn: psycopg.Connection[Any],
    session_id: str,
    event_ticker: str | None,
) -> tuple[ReplayRuleSnapshot, ...]:
    if not event_ticker:
        return ()
    rows = conn.execute(
        """
        SELECT id,event_ticker,captured_at,rules_hash,raw_payload
          FROM settlement_rule_snapshots
         WHERE session_id=%s AND event_ticker=%s
        """,
        (session_id, event_ticker),
    ).fetchall()
    return tuple(ReplayRuleSnapshot(
        snapshot_id=int(row[0]),
        event_ticker=str(row[1]),
        captured_at=_aware(row[2]),
        rules_hash=str(row[3]),
        raw_payload=_mapping(row[4]),
    ) for row in rows)


def _event_from_rule_snapshot(snapshot: ReplayRuleSnapshot, state: HardClimateState) -> dict[str, Any] | None:
    payload = _mapping(snapshot.raw_payload)
    raw_event = payload.get("event")
    if not isinstance(raw_event, Mapping):
        return None
    markets_raw = raw_event.get("markets")
    if not isinstance(markets_raw, Sequence) or isinstance(markets_raw, (str, bytes)):
        return None
    active_markets = [
        dict(market) for market in markets_raw
        if isinstance(market, Mapping) and _active_at(market, state.first_known_at)
    ]
    if not active_markets:
        return None
    return {
        "event_ticker": snapshot.event_ticker,
        "station_code": state.station_code,
        "rules_hash": snapshot.rules_hash,
        "markets": active_markets,
    }


def _active_at(market: Mapping[str, Any], when: datetime) -> bool:
    opened = _parse_time(market.get("open_time"))
    closed = _parse_time(market.get("close_time") or market.get("expected_expiration_time"))
    if opened is not None and when < opened:
        return False
    if closed is not None and when > closed:
        return False
    return True


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _no_elimination(state: HardClimateState, reason: str) -> ReplayTransitionElimination:
    return ReplayTransitionElimination(
        state_id=state.state_id,
        known_at=state.first_known_at,
        rule_snapshot_id=None,
        rule_rules_hash=None,
        accepted=False,
        fail_closed_reason=reason,
        dead_market_tickers=(),
    )


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("expected JSON object")
        return parsed
    return dict(value)


def _aware(value: Any) -> datetime:
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value
