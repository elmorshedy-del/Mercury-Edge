from __future__ import annotations

"""Step 4G-C2 transport qualification contract for MADIS OMO.

This module deliberately stops short of implementing an LDM network client. It
defines the only supported boundary a future live receiver/importer should use:

    exact received bytes -> immutable RawCapture -> RawSourceRecord -> parser

Live LDM receipt and historical/archive import are distinct origins. Archive
metadata may be useful for representation calibration, but it can never be
relabeled as a contemporaneous live receipt. Reconnects/sequence gaps are
separate immutable transport events so replay can fail closed across coverage
holes instead of mistaking silence for complete observation.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping

import psycopg

from hard_information_domain import RawSourceRecord
from madis_omo import (
    MADIS_OMO_SOURCE,
    ContractMadisOmoAdapter,
    MadisAdapterResult,
)
from raw_journal import RawCapture, canonical_json_bytes, insert_raw_capture, raw_source_record

MADIS_LIVE_STREAM = "madis_omo_ldm"
MADIS_ARCHIVE_STREAM = "madis_omo_archive"
MADIS_TRANSPORT_MODEL_VERSION = "madis-transport-contract-v1"
TRANSPORT_EVENT_MODEL_VERSION = "source-transport-events-v1"


class MadisDataOrigin(str, Enum):
    LIVE_LDM = "live_ldm"
    ARCHIVE_IMPORT = "archive_import"


class TransportEventType(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTED = "reconnected"
    SEQUENCE_GAP = "sequence_gap"
    QUEUE_GAP = "queue_gap"
    UNKNOWN_COVERAGE_GAP = "unknown_coverage_gap"


@dataclass(frozen=True)
class MadisTransportEnvelope:
    """One transport receipt before any semantic MADIS field parsing."""

    session_id: str
    origin: MadisDataOrigin
    raw_bytes: bytes
    received_at: datetime
    received_epoch_ns: int
    received_monotonic_ns: int
    product_id: str
    connection_id: str | None = None
    sequence_key: str | None = None
    reconnect_generation: int | None = None
    station_code: str | None = None
    observed_at: datetime | None = None
    source_published_at: datetime | None = None
    first_fetchable_at: datetime | None = None
    content_type: str = "application/octet-stream"
    content_encoding: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.raw_bytes, bytes):
            raise TypeError("MADIS transport payload must be exact bytes")
        if not self.session_id.strip():
            raise ValueError("session_id is required")
        if not self.product_id.strip():
            raise ValueError("product_id is required")
        if self.received_epoch_ns < 0 or self.received_monotonic_ns < 0:
            raise ValueError("receipt clocks must be non-negative")
        if self.reconnect_generation is not None and self.reconnect_generation < 0:
            raise ValueError("reconnect_generation must be non-negative")

    @property
    def live_causal(self) -> bool:
        return self.origin is MadisDataOrigin.LIVE_LDM

    @property
    def source_stream(self) -> str:
        return MADIS_LIVE_STREAM if self.live_causal else MADIS_ARCHIVE_STREAM

    @property
    def transport(self) -> str:
        return "ldm" if self.live_causal else "archive_import"

    def to_raw_capture(self) -> RawCapture:
        # For archive imports, a historical source timestamp is not proof of
        # contemporaneous fetchability. Preserve any claimed value only as raw
        # metadata and deliberately keep first_fetchable_at unset.
        effective_first_fetchable = self.first_fetchable_at if self.live_causal else None
        metadata = {
            **dict(self.metadata),
            "madis_data_origin": self.origin.value,
            "live_causal": self.live_causal,
            "product_id": self.product_id,
            "connection_id": self.connection_id,
            "reconnect_generation": self.reconnect_generation,
            "transport_model_version": MADIS_TRANSPORT_MODEL_VERSION,
        }
        if not self.live_causal and self.first_fetchable_at is not None:
            metadata["archive_claimed_first_fetchable_at"] = self.first_fetchable_at.isoformat()

        return RawCapture(
            session_id=self.session_id,
            source=MADIS_OMO_SOURCE,
            source_stream=self.source_stream,
            raw_bytes=self.raw_bytes,
            received_at=self.received_at,
            received_epoch_ns=self.received_epoch_ns,
            received_monotonic_ns=self.received_monotonic_ns,
            transport=self.transport,
            content_type=self.content_type,
            station_code=self.station_code,
            observed_at=self.observed_at,
            source_published_at=self.source_published_at,
            first_fetchable_at=effective_first_fetchable,
            sequence_key=self.sequence_key,
            content_encoding=self.content_encoding,
            metadata=metadata,
        )


@dataclass(frozen=True)
class CapturedMadisRecord:
    """Proof that raw bytes were persisted before semantic parsing."""

    raw_source_id: int
    capture: RawCapture
    raw_record: RawSourceRecord
    origin: MadisDataOrigin
    product_id: str
    connection_id: str | None
    reconnect_generation: int | None
    transport_model_version: str = MADIS_TRANSPORT_MODEL_VERSION

    @property
    def live_causal(self) -> bool:
        return self.origin is MadisDataOrigin.LIVE_LDM


@dataclass(frozen=True)
class SourceTransportEvent:
    session_id: str
    source: str
    source_stream: str
    event_type: TransportEventType
    detected_at: datetime
    detected_epoch_ns: int
    detected_monotonic_ns: int
    connection_id: str | None = None
    interval_start_at: datetime | None = None
    interval_end_at: datetime | None = None
    prior_sequence_key: str | None = None
    next_sequence_key: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    model_version: str = TRANSPORT_EVENT_MODEL_VERSION

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not self.source.strip() or not self.source_stream.strip():
            raise ValueError("session/source/source_stream are required")
        if self.detected_epoch_ns < 0 or self.detected_monotonic_ns < 0:
            raise ValueError("transport event clocks must be non-negative")
        if self.interval_start_at is not None and self.interval_end_at is not None:
            if self.interval_end_at < self.interval_start_at:
                raise ValueError("transport gap end cannot precede start")

    @property
    def event_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source": self.source,
            "source_stream": self.source_stream,
            "event_type": self.event_type.value,
            "connection_id": self.connection_id,
            "detected_at": self.detected_at.isoformat(),
            "detected_epoch_ns": self.detected_epoch_ns,
            "detected_monotonic_ns": self.detected_monotonic_ns,
            "interval_start_at": self.interval_start_at.isoformat() if self.interval_start_at else None,
            "interval_end_at": self.interval_end_at.isoformat() if self.interval_end_at else None,
            "prior_sequence_key": self.prior_sequence_key,
            "next_sequence_key": self.next_sequence_key,
            "details": dict(self.details),
            "model_version": self.model_version,
        }

    @property
    def event_sha256(self) -> str:
        return sha256(canonical_json_bytes(self.event_payload)).hexdigest()

    @property
    def event_id(self) -> str:
        identity = "|".join((
            self.session_id,
            self.source,
            self.source_stream,
            self.event_type.value,
            self.connection_id or "",
            str(self.detected_epoch_ns),
            self.prior_sequence_key or "",
            self.next_sequence_key or "",
            self.event_sha256,
            self.model_version,
        )).encode("utf-8")
        return f"transport:{sha256(identity).hexdigest()}"


def persist_madis_transport_record(
    conn: psycopg.Connection[Any],
    envelope: MadisTransportEnvelope,
) -> CapturedMadisRecord:
    """Persist exact bytes first and return the only parser-facing record type."""
    capture = envelope.to_raw_capture()
    raw_source_id = insert_raw_capture(conn, capture)
    record = raw_source_record(capture, raw_source_id)
    return CapturedMadisRecord(
        raw_source_id=raw_source_id,
        capture=capture,
        raw_record=record,
        origin=envelope.origin,
        product_id=envelope.product_id,
        connection_id=envelope.connection_id,
        reconnect_generation=envelope.reconnect_generation,
    )


def parse_captured_madis_omo(
    captured: CapturedMadisRecord,
    *,
    station_timezone: str,
    mercury_interpreted_at: datetime,
    fields: Mapping[str, Any],
    adapter: ContractMadisOmoAdapter | None = None,
) -> MadisAdapterResult:
    """Parse only an already-persisted capture; annotate live/archive causality."""
    parser = adapter or ContractMadisOmoAdapter()
    result = parser.parse_minute(
        raw_record=captured.raw_record,
        station_timezone=station_timezone,
        mercury_interpreted_at=mercury_interpreted_at,
        fields=fields,
    )
    minute = replace(
        result.minute,
        metadata={
            **dict(result.minute.metadata),
            "madis_data_origin": captured.origin.value,
            "live_causal": captured.live_causal,
            "transport_product_id": captured.product_id,
            "transport_connection_id": captured.connection_id,
            "transport_reconnect_generation": captured.reconnect_generation,
            "raw_source_id": captured.raw_source_id,
            "transport_model_version": captured.transport_model_version,
        },
    )
    return replace(result, minute=minute)


def persist_source_transport_event(
    conn: psycopg.Connection[Any],
    event: SourceTransportEvent,
) -> str:
    """Append one immutable continuity event; deterministic retries are idempotent."""
    details_json = json.dumps(
        dict(event.details), sort_keys=True, separators=(",", ":"), default=str
    )
    row = conn.execute(
        """
        INSERT INTO source_transport_events(
          event_id,session_id,source,source_stream,event_type,connection_id,
          detected_at,detected_epoch_ns,detected_monotonic_ns,
          interval_start_at,interval_end_at,prior_sequence_key,next_sequence_key,
          model_version,details,event_sha256
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
        )
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """,
        (
            event.event_id,
            event.session_id,
            event.source,
            event.source_stream,
            event.event_type.value,
            event.connection_id,
            event.detected_at,
            str(event.detected_epoch_ns),
            str(event.detected_monotonic_ns),
            event.interval_start_at,
            event.interval_end_at,
            event.prior_sequence_key,
            event.next_sequence_key,
            event.model_version,
            details_json,
            event.event_sha256,
        ),
    ).fetchone()
    if row:
        return str(row[0])

    existing = conn.execute(
        "SELECT event_sha256 FROM source_transport_events WHERE event_id=%s",
        (event.event_id,),
    ).fetchone()
    if not existing:
        raise RuntimeError("transport event insert returned no row and existing event was not found")
    if str(existing[0]) != event.event_sha256:
        raise RuntimeError("transport event identity collision or non-deterministic payload")
    return event.event_id
