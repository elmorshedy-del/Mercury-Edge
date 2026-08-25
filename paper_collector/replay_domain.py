from __future__ import annotations

"""Step 4J-A deterministic causal replay contract.

The event stream models *availability to Mercury*, not physical observation time.
It is intentionally source-neutral and does not execute strategy logic.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping, Sequence

import psycopg

from raw_journal import canonical_json_bytes

REPLAY_ENGINE_VERSION = "canonical-causal-replay-v1"
REPLAY_MANIFEST_VERSION = "replay-manifest-v1"


class ReplayPolicy(str, Enum):
    BENCHMARK = "benchmark"
    RESEARCH = "research"


class ReplayEventKind(str, Enum):
    RAW_SOURCE = "raw_source"
    MARKET_MESSAGE = "market_message"
    RULE_SNAPSHOT = "rule_snapshot"
    TRANSPORT_EVENT = "transport_event"
    VALIDATION_PRODUCT = "validation_product"
    EXCHANGE_SETTLEMENT = "exchange_settlement"


_KIND_RANK = {
    ReplayEventKind.RAW_SOURCE: 10,
    ReplayEventKind.TRANSPORT_EVENT: 20,
    ReplayEventKind.RULE_SNAPSHOT: 30,
    ReplayEventKind.MARKET_MESSAGE: 40,
    ReplayEventKind.VALIDATION_PRODUCT: 50,
    ReplayEventKind.EXCHANGE_SETTLEMENT: 60,
}


@dataclass(frozen=True)
class ReplayVersionBundle:
    parser_version: str
    calendar_version: str
    evidence_model_version: str
    hard_state_version: str
    elimination_version: str
    execution_version: str
    replay_engine_version: str = REPLAY_ENGINE_VERSION

    def __post_init__(self) -> None:
        for value in self.to_dict().values():
            if not str(value).strip():
                raise ValueError("all replay component versions are required")

    def to_dict(self) -> dict[str, str]:
        return {
            "parser_version": self.parser_version,
            "calendar_version": self.calendar_version,
            "evidence_model_version": self.evidence_model_version,
            "hard_state_version": self.hard_state_version,
            "elimination_version": self.elimination_version,
            "execution_version": self.execution_version,
            "replay_engine_version": self.replay_engine_version,
        }


CURRENT_BENCHMARK_VERSIONS = ReplayVersionBundle(
    parser_version="asos-metar-evidence-v1",
    calendar_version="lst-climate-calendar-v1",
    evidence_model_version="raw-asos-lattice-v1",
    hard_state_version="hard-state-accumulator-v1",
    elimination_version="bucket-elimination-v1",
    execution_version="canonical-dead-no-paper-v1",
)


@dataclass(frozen=True)
class ReplayFilter:
    station_code: str | None = None
    event_ticker: str | None = None
    climate_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "station_code": self.station_code,
            "event_ticker": self.event_ticker,
            "climate_date": self.climate_date.isoformat() if self.climate_date else None,
        }


@dataclass(frozen=True)
class ReplayEvent:
    kind: ReplayEventKind
    source_id: str
    available_at: datetime
    available_epoch_ns: int | None
    payload_sha256: str
    source: str
    source_stream: str | None = None
    station_code: str | None = None
    event_ticker: str | None = None
    market_ticker: str | None = None
    sequence_key: str | None = None
    live_causal: bool = True
    benchmark_admissible: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.available_at.tzinfo is None:
            raise ValueError("replay availability time must be timezone-aware")
        if self.available_epoch_ns is not None and self.available_epoch_ns < 0:
            raise ValueError("replay epoch-ns must be non-negative")
        if not self.source_id.strip() or not self.payload_sha256.strip():
            raise ValueError("source id and payload hash are required")

    @property
    def sort_key(self) -> tuple[int, int, str, str]:
        epoch_ns = (
            int(self.available_epoch_ns)
            if self.available_epoch_ns is not None
            else int(self.available_at.timestamp() * 1_000_000_000)
        )
        return (epoch_ns, _KIND_RANK[self.kind], self.sequence_key or "", self.source_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "source_id": self.source_id,
            "available_at": self.available_at.astimezone(timezone.utc).isoformat(),
            "available_epoch_ns": self.available_epoch_ns,
            "payload_sha256": self.payload_sha256,
            "source": self.source,
            "source_stream": self.source_stream,
            "station_code": self.station_code,
            "event_ticker": self.event_ticker,
            "market_ticker": self.market_ticker,
            "sequence_key": self.sequence_key,
            "live_causal": self.live_causal,
            "benchmark_admissible": self.benchmark_admissible,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ReplayManifest:
    source_session_id: str
    versions: ReplayVersionBundle
    policy: ReplayPolicy
    replay_filter: ReplayFilter
    input_event_count: int
    source_input_sha256: str
    manifest_version: str = REPLAY_MANIFEST_VERSION

    @property
    def manifest_id(self) -> str:
        payload = {
            "manifest_version": self.manifest_version,
            "source_session_id": self.source_session_id,
            "versions": self.versions.to_dict(),
            "policy": self.policy.value,
            "filter": self.replay_filter.to_dict(),
            "input_event_count": self.input_event_count,
            "source_input_sha256": self.source_input_sha256,
        }
        return f"replay:{sha256(canonical_json_bytes(payload)).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "manifest_version": self.manifest_version,
            "source_session_id": self.source_session_id,
            "versions": self.versions.to_dict(),
            "policy": self.policy.value,
            "filter": self.replay_filter.to_dict(),
            "input_event_count": self.input_event_count,
            "source_input_sha256": self.source_input_sha256,
        }


def sort_replay_events(events: Iterable[ReplayEvent]) -> tuple[ReplayEvent, ...]:
    return tuple(sorted(events, key=lambda event: event.sort_key))


def source_input_hash(events: Iterable[ReplayEvent]) -> str:
    ordered = sort_replay_events(events)
    material = [event.to_dict() for event in ordered]
    return sha256(canonical_json_bytes(material)).hexdigest()


def build_manifest(
    *,
    source_session_id: str,
    versions: ReplayVersionBundle,
    policy: ReplayPolicy,
    replay_filter: ReplayFilter,
    events: Sequence[ReplayEvent],
) -> ReplayManifest:
    if not source_session_id.strip():
        raise ValueError("source session id is required")
    return ReplayManifest(
        source_session_id=source_session_id,
        versions=versions,
        policy=policy,
        replay_filter=replay_filter,
        input_event_count=len(events),
        source_input_sha256=source_input_hash(events),
    )


def load_replay_events(
    conn: psycopg.Connection[Any],
    *,
    source_session_id: str,
    replay_filter: ReplayFilter | None = None,
) -> tuple[ReplayEvent, ...]:
    """Load immutable source facts and normalize them into causal events.

    SQL ordering is deliberately irrelevant; final ordering is purely the
    canonical :attr:`ReplayEvent.sort_key`.
    """
    filt = replay_filter or ReplayFilter()
    events: list[ReplayEvent] = []
    events.extend(_raw_source_events(conn, source_session_id, filt))
    events.extend(_market_events(conn, source_session_id, filt))
    events.extend(_rule_events(conn, source_session_id, filt))
    events.extend(_transport_events(conn, source_session_id, filt))
    events.extend(_validation_events(conn, source_session_id, filt))
    events.extend(_exchange_settlement_events(conn, source_session_id, filt))
    return sort_replay_events(events)


def benchmark_events(events: Iterable[ReplayEvent]) -> tuple[ReplayEvent, ...]:
    """Events admissible as benchmark-world inputs (later audit events remain present)."""
    return tuple(event for event in sort_replay_events(events) if event.benchmark_admissible)


def _raw_source_events(conn: psycopg.Connection[Any], session_id: str, filt: ReplayFilter) -> list[ReplayEvent]:
    rows = conn.execute(
        """
        SELECT id,source,source_stream,station_code,observed_at,first_fetchable_at,
               received_at,received_epoch_ns,transport,sequence_key,payload_sha256,metadata
        FROM raw_source_journal
        WHERE session_id=%s
        """,
        (session_id,),
    ).fetchall()
    out: list[ReplayEvent] = []
    for row in rows:
        (
            row_id, source, stream, station, observed_at, first_fetchable_at,
            received_at, received_epoch_ns, transport, sequence_key, payload_hash,
            metadata_raw,
        ) = row
        if filt.station_code and station is not None and str(station).upper() != filt.station_code.upper():
            continue
        metadata = _mapping(metadata_raw)
        archive = (
            str(transport) == "archive_import"
            or str(stream) == "madis_omo_archive"
            or metadata.get("live_causal") is False
        )
        out.append(ReplayEvent(
            kind=ReplayEventKind.RAW_SOURCE,
            source_id=f"raw_source_journal:{int(row_id)}",
            available_at=_aware(received_at),
            available_epoch_ns=int(received_epoch_ns),
            payload_sha256=str(payload_hash),
            source=str(source),
            source_stream=str(stream),
            station_code=str(station) if station is not None else None,
            sequence_key=str(sequence_key) if sequence_key is not None else None,
            live_causal=not archive,
            benchmark_admissible=not archive,
            metadata={
                "transport": str(transport),
                "observed_at": _iso(observed_at),
                "first_fetchable_at": _iso(first_fetchable_at),
                "archive_import": archive,
            },
        ))
    return out


def _market_events(conn: psycopg.Connection[Any], session_id: str, filt: ReplayFilter) -> list[ReplayEvent]:
    rows = conn.execute(
        """
        SELECT id,channel,market_ticker,received_at,received_epoch_ns,payload_sha256,
               connection_id::text,sid,seq
        FROM market_data_journal
        WHERE session_id=%s
        """,
        (session_id,),
    ).fetchall()
    out: list[ReplayEvent] = []
    for row_id, channel, market, received_at, received_ns, digest, connection_id, sid, seq in rows:
        market_text = str(market) if market is not None else None
        # Keep the complete session WebSocket chain in the replay manifest even
        # when a row belongs to another event. L2 integrity validation consumes
        # connection-global hash/sequence continuity, so every market row that
        # can influence execution must be bound into source_input_sha256.
        seq_key = f"{connection_id or ''}:{sid if sid is not None else ''}:{seq if seq is not None else ''}:{int(row_id)}"
        out.append(ReplayEvent(
            kind=ReplayEventKind.MARKET_MESSAGE,
            source_id=f"market_data_journal:{int(row_id)}",
            available_at=_aware(received_at),
            available_epoch_ns=int(received_ns),
            payload_sha256=str(digest),
            source="KALSHI_WS",
            source_stream=str(channel),
            event_ticker=filt.event_ticker if filt.event_ticker and market_text and market_text.startswith(filt.event_ticker) else None,
            market_ticker=market_text,
            sequence_key=seq_key,
            metadata={"connection_id": connection_id, "sid": sid, "seq": seq},
        ))
    return out


def _rule_events(conn: psycopg.Connection[Any], session_id: str, filt: ReplayFilter) -> list[ReplayEvent]:
    rows = conn.execute(
        """
        SELECT id,series_ticker,event_ticker,captured_at,rules_hash,raw_payload
        FROM settlement_rule_snapshots
        WHERE session_id=%s
        """,
        (session_id,),
    ).fetchall()
    out: list[ReplayEvent] = []
    for row_id, series, event, captured_at, rules_hash, raw_payload in rows:
        event_text = str(event) if event is not None else None
        if filt.event_ticker and event_text is not None and event_text != filt.event_ticker:
            continue
        payload_hash = sha256(canonical_json_bytes(_mapping(raw_payload))).hexdigest()
        out.append(ReplayEvent(
            kind=ReplayEventKind.RULE_SNAPSHOT,
            source_id=f"settlement_rule_snapshots:{int(row_id)}",
            available_at=_aware(captured_at),
            available_epoch_ns=None,
            payload_sha256=payload_hash,
            source="KALSHI_REST",
            source_stream="rule_snapshot",
            event_ticker=event_text,
            sequence_key=f"{series}:{event_text or ''}:{int(row_id)}",
            metadata={"series_ticker": str(series), "rules_hash": str(rules_hash)},
        ))
    return out


def _transport_events(conn: psycopg.Connection[Any], session_id: str, filt: ReplayFilter) -> list[ReplayEvent]:
    rows = conn.execute(
        """
        SELECT event_id,source,source_stream,event_type,detected_at,detected_epoch_ns,
               connection_id,prior_sequence_key,next_sequence_key,event_sha256,details
        FROM source_transport_events
        WHERE session_id=%s
        """,
        (session_id,),
    ).fetchall()
    out: list[ReplayEvent] = []
    for event_id, source, stream, event_type, detected_at, detected_ns, connection_id, prior_seq, next_seq, digest, details in rows:
        out.append(ReplayEvent(
            kind=ReplayEventKind.TRANSPORT_EVENT,
            source_id=f"source_transport_events:{event_id}",
            available_at=_aware(detected_at),
            available_epoch_ns=int(detected_ns),
            payload_sha256=str(digest),
            source=str(source),
            source_stream=str(stream),
            sequence_key=f"{connection_id or ''}:{prior_seq or ''}:{next_seq or ''}:{event_id}",
            metadata={"event_type": str(event_type), "details": _mapping(details)},
        ))
    return out


def _validation_events(conn: psycopg.Connection[Any], session_id: str, filt: ReplayFilter) -> list[ReplayEvent]:
    rows = conn.execute(
        """
        SELECT validation_id,source,source_product_id,station_code,climate_date,
               mercury_received_at,product_sha256,lifecycle,authority
        FROM validation_products
        WHERE session_id=%s
        """,
        (session_id,),
    ).fetchall()
    out: list[ReplayEvent] = []
    for validation_id, source, product_id, station, climate_day, received_at, digest, lifecycle, authority in rows:
        if filt.station_code and str(station).upper() != filt.station_code.upper():
            continue
        if filt.climate_date and climate_day is not None and _date(climate_day) != filt.climate_date:
            continue
        out.append(ReplayEvent(
            kind=ReplayEventKind.VALIDATION_PRODUCT,
            source_id=f"validation_products:{validation_id}",
            available_at=_aware(received_at),
            available_epoch_ns=None,
            payload_sha256=str(digest),
            source=str(source),
            source_stream="validation_product",
            station_code=str(station),
            sequence_key=str(product_id),
            metadata={"climate_date": _date(climate_day).isoformat() if climate_day else None, "lifecycle": str(lifecycle), "authority": str(authority)},
        ))
    return out


def _exchange_settlement_events(conn: psycopg.Connection[Any], session_id: str, filt: ReplayFilter) -> list[ReplayEvent]:
    rows = conn.execute(
        """
        SELECT exchange_settlement_id,event_ticker,station_code,climate_date,
               captured_at,settlement_sha256,rules_hash
        FROM exchange_market_settlements
        WHERE session_id=%s
        """,
        (session_id,),
    ).fetchall()
    out: list[ReplayEvent] = []
    for settlement_id, event, station, climate_day, captured_at, digest, rules_hash in rows:
        if filt.event_ticker and str(event) != filt.event_ticker:
            continue
        if filt.station_code and str(station).upper() != filt.station_code.upper():
            continue
        if filt.climate_date and _date(climate_day) != filt.climate_date:
            continue
        out.append(ReplayEvent(
            kind=ReplayEventKind.EXCHANGE_SETTLEMENT,
            source_id=f"exchange_market_settlements:{settlement_id}",
            available_at=_aware(captured_at),
            available_epoch_ns=None,
            payload_sha256=str(digest),
            source="KALSHI_SETTLEMENT",
            source_stream="exchange_market_settlement",
            station_code=str(station),
            event_ticker=str(event),
            sequence_key=str(settlement_id),
            metadata={"climate_date": _date(climate_day).isoformat(), "rules_hash": str(rules_hash)},
        ))
    return out


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError("expected JSON object")
        return decoded
    return dict(value)


def _aware(value: Any) -> datetime:
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("database causal timestamp must be timezone-aware")
    return value


def _iso(value: Any) -> str | None:
    return _aware(value).isoformat() if value is not None else None


def _date(value: Any) -> date:
    return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value))
