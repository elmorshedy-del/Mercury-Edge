from __future__ import annotations

"""Append-only raw capture and evidence-derivation persistence.

The central invariant is that source bytes never change. A parser/evidence
model may be replaced, but a replacement writes a distinct versioned
derivation over the same immutable capture.
"""

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

import psycopg

from hard_information_domain import RawSourceRecord, SettlementEvidence, SourceClocks

RAW_JOURNAL_VERSION = "raw-source-journal-v1"
DERIVATION_JOURNAL_VERSION = "evidence-derivation-journal-v1"


@dataclass(frozen=True)
class RawCapture:
    session_id: str
    source: str
    source_stream: str
    raw_bytes: bytes
    received_at: datetime
    received_epoch_ns: int
    received_monotonic_ns: int
    transport: str
    content_type: str
    station_code: str | None = None
    observed_at: datetime | None = None
    source_published_at: datetime | None = None
    first_fetchable_at: datetime | None = None
    sequence_key: str | None = None
    content_encoding: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def payload_sha256(self) -> str:
        return sha256_hex(self.raw_bytes)

    @property
    def capture_id(self) -> str:
        # Receipt time is deliberately part of the identity. Two identical
        # network payloads received at different times are two causal captures.
        key = "|".join((
            RAW_JOURNAL_VERSION,
            self.session_id,
            self.source,
            self.source_stream,
            str(self.received_epoch_ns),
            self.payload_sha256,
        )).encode("utf-8")
        return f"raw:{sha256(key).hexdigest()}"


def sha256_hex(payload: bytes) -> str:
    if not isinstance(payload, bytes):
        raise TypeError("raw source payload must be bytes")
    return sha256(payload).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")


def insert_raw_capture(conn: psycopg.Connection[Any], capture: RawCapture) -> int:
    """Insert one immutable network capture, idempotently by capture_id.

    This function never updates a capture. A retry of the same capture returns
    the original row id; a later receipt of identical bytes has a distinct id.
    """
    row = conn.execute(
        """
        INSERT INTO raw_source_journal(
          capture_id,session_id,source,source_stream,station_code,observed_at,
          source_published_at,first_fetchable_at,received_at,received_epoch_ns,
          received_monotonic_ns,transport,sequence_key,content_type,
          content_encoding,raw_bytes,payload_sha256,metadata
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
        )
        ON CONFLICT (capture_id) DO NOTHING
        RETURNING id
        """,
        (
            capture.capture_id,
            capture.session_id,
            capture.source,
            capture.source_stream,
            capture.station_code,
            capture.observed_at,
            capture.source_published_at,
            capture.first_fetchable_at,
            capture.received_at,
            str(capture.received_epoch_ns),
            str(capture.received_monotonic_ns),
            capture.transport,
            capture.sequence_key,
            capture.content_type,
            capture.content_encoding,
            capture.raw_bytes,
            capture.payload_sha256,
            json.dumps(capture.metadata, sort_keys=True, separators=(",", ":"), default=_json_default),
        ),
    ).fetchone()
    if row:
        return int(row[0])

    existing = conn.execute(
        "SELECT id,payload_sha256 FROM raw_source_journal WHERE capture_id=%s",
        (capture.capture_id,),
    ).fetchone()
    if not existing:
        raise RuntimeError("raw capture insert returned no row and existing capture was not found")
    if str(existing[1]) != capture.payload_sha256:
        raise RuntimeError("raw capture identity collision")
    return int(existing[0])


def raw_source_record(capture: RawCapture, row_id: int) -> RawSourceRecord:
    return RawSourceRecord(
        record_id=f"raw_source_journal:{int(row_id)}",
        source=capture.source,
        station_code=capture.station_code,
        payload_hash=capture.payload_sha256,
        clocks=SourceClocks(
            observed_at=capture.observed_at or capture.received_at,
            mercury_received_at=capture.received_at,
            source_published_at=capture.source_published_at,
            first_fetchable_at=capture.first_fetchable_at,
            mercury_interpreted_at=None,
        ),
        transport=capture.transport,
        sequence_key=capture.sequence_key,
    )


def persist_evidence_derivation(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    evidence: SettlementEvidence,
    raw_source_ids: Sequence[int],
) -> str:
    """Persist a versioned evidence interpretation without mutating history."""
    if not raw_source_ids:
        raise ValueError("evidence derivation requires at least one immutable raw source id")

    payload = evidence.to_dict()
    payload["derivation_journal_version"] = DERIVATION_JOURNAL_VERSION
    canonical = canonical_json_bytes(payload)
    derivation_sha = sha256_hex(canonical)

    conn.execute(
        """
        INSERT INTO evidence_derivations(
          evidence_id,session_id,station_code,climate_date,evidence_type,trust,
          integrity_status,proven_min_f,proven_max_f,possible_canonical_f,
          raw_identifier,observed_at,source_published_at,first_fetchable_at,
          mercury_received_at,mercury_interpreted_at,parser_version,
          evidence_model_version,calendar_version,derivation_payload,
          derivation_sha256
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
        )
        ON CONFLICT (evidence_id) DO NOTHING
        """,
        (
            evidence.evidence_id,
            session_id,
            evidence.station_code,
            evidence.climate_date,
            evidence.evidence_type.value,
            evidence.trust.value,
            evidence.integrity_status.value,
            evidence.proven_min_f,
            evidence.proven_max_f,
            json.dumps(list(evidence.possible_canonical_f), separators=(",", ":")),
            evidence.raw_identifier,
            evidence.clocks.observed_at,
            evidence.clocks.source_published_at,
            evidence.clocks.first_fetchable_at,
            evidence.clocks.mercury_received_at,
            evidence.clocks.mercury_interpreted_at,
            evidence.parser_version,
            evidence.evidence_model_version,
            evidence.calendar_version,
            canonical.decode("utf-8"),
            derivation_sha,
        ),
    )

    existing = conn.execute(
        "SELECT derivation_sha256 FROM evidence_derivations WHERE evidence_id=%s",
        (evidence.evidence_id,),
    ).fetchone()
    if not existing or str(existing[0]) != derivation_sha:
        raise RuntimeError("evidence id collision or non-deterministic derivation")

    expected_links = tuple(dict.fromkeys(int(raw_id) for raw_id in raw_source_ids))
    for ordinal, raw_id in enumerate(expected_links):
        conn.execute(
            """
            INSERT INTO evidence_source_links(evidence_id,raw_source_id,ordinal,relation)
            VALUES (%s,%s,%s,'input')
            ON CONFLICT DO NOTHING
            """,
            (evidence.evidence_id, raw_id, ordinal),
        )

    persisted_links = conn.execute(
        """
        SELECT raw_source_id FROM evidence_source_links
         WHERE evidence_id=%s AND relation='input'
         ORDER BY ordinal ASC
        """,
        (evidence.evidence_id,),
    ).fetchall()
    actual_links = tuple(int(row[0]) for row in persisted_links)
    if actual_links != expected_links:
        raise RuntimeError("evidence source-link mismatch")
    return evidence.evidence_id


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
