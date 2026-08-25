from __future__ import annotations

"""Step 4I-B structured, append-only hard-edge failure/non-admission events."""

from dataclasses import dataclass, field
from datetime import date, datetime
from hashlib import sha256
from typing import Any, Mapping

import psycopg

from raw_journal import canonical_json_bytes, sha256_hex

FAILURE_MODEL_VERSION = "hard-edge-failure-ledger-v1"
VALID_STAGES = {
    "source_parse", "evidence", "hard_state", "elimination", "execution",
    "validation", "settlement", "replay",
}
VALID_DISPOSITIONS = {
    "integrity_failure", "fail_closed", "non_admission", "economic_skip",
    "invariant_failure",
}


@dataclass(frozen=True)
class HardEdgeFailureEvent:
    session_id: str
    stage: str
    disposition_class: str
    reason_code: str
    occurred_at: datetime
    station_code: str | None = None
    climate_date: date | None = None
    event_ticker: str | None = None
    market_ticker: str | None = None
    raw_source_id: int | None = None
    evidence_id: str | None = None
    state_id: str | None = None
    elimination_id: str | None = None
    signal_id: int | None = None
    order_id: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    failure_model_version: str = FAILURE_MODEL_VERSION

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("failure event session_id is required")
        if self.stage not in VALID_STAGES:
            raise ValueError("unsupported failure event stage")
        if self.disposition_class not in VALID_DISPOSITIONS:
            raise ValueError("unsupported failure disposition")
        if not self.reason_code or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in self.reason_code):
            raise ValueError("reason_code must be stable uppercase snake case")
        if self.raw_source_id is not None and int(self.raw_source_id) <= 0:
            raise ValueError("raw_source_id must be positive")

    @property
    def failure_id(self) -> str:
        identity = "|".join((
            self.failure_model_version,
            self.session_id,
            self.stage,
            self.disposition_class,
            self.reason_code,
            self.station_code or "",
            self.climate_date.isoformat() if self.climate_date else "",
            self.event_ticker or "",
            self.market_ticker or "",
            str(self.raw_source_id) if self.raw_source_id is not None else "",
            self.evidence_id or "",
            self.state_id or "",
            self.elimination_id or "",
            str(self.signal_id) if self.signal_id is not None else "",
            str(self.order_id) if self.order_id is not None else "",
            self.occurred_at.isoformat(),
        )).encode("utf-8")
        return f"hard-edge-failure:{sha256(identity).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "session_id": self.session_id,
            "stage": self.stage,
            "disposition_class": self.disposition_class,
            "reason_code": self.reason_code,
            "station_code": self.station_code,
            "climate_date": self.climate_date.isoformat() if self.climate_date else None,
            "event_ticker": self.event_ticker,
            "market_ticker": self.market_ticker,
            "raw_source_id": self.raw_source_id,
            "evidence_id": self.evidence_id,
            "state_id": self.state_id,
            "elimination_id": self.elimination_id,
            "signal_id": self.signal_id,
            "order_id": self.order_id,
            "occurred_at": self.occurred_at.isoformat(),
            "failure_model_version": self.failure_model_version,
            "details": dict(self.details),
        }


def persist_failure_event(
    conn: psycopg.Connection[Any],
    *,
    event: HardEdgeFailureEvent,
) -> str:
    """Persist idempotently; same identity with different payload fails closed."""
    if event.raw_source_id is not None:
        row = conn.execute(
            "SELECT session_id,station_code FROM raw_source_journal WHERE id=%s",
            (int(event.raw_source_id),),
        ).fetchone()
        if not row:
            raise ValueError("failure event raw source not found")
        if str(row[0]) != event.session_id:
            raise ValueError("failure event raw source belongs to different session")
        if event.station_code and row[1] is not None and str(row[1]).upper() != event.station_code.upper():
            raise ValueError("failure event raw source station mismatch")

    payload = {
        "journal": FAILURE_MODEL_VERSION,
        "event": event.to_dict(),
    }
    canonical = canonical_json_bytes(payload)
    digest = sha256_hex(canonical)
    conn.execute(
        """
        INSERT INTO hard_edge_failure_events(
          failure_id,session_id,stage,disposition_class,reason_code,station_code,
          climate_date,event_ticker,market_ticker,raw_source_id,evidence_id,state_id,
          elimination_id,signal_id,order_id,occurred_at,failure_model_version,
          details,failure_payload,failure_sha256
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s
        )
        ON CONFLICT (failure_id) DO NOTHING
        """,
        (
            event.failure_id, event.session_id, event.stage, event.disposition_class,
            event.reason_code, event.station_code, event.climate_date,
            event.event_ticker, event.market_ticker, event.raw_source_id,
            event.evidence_id, event.state_id, event.elimination_id,
            event.signal_id, event.order_id, event.occurred_at,
            event.failure_model_version,
            canonical_json_bytes(dict(event.details)).decode("utf-8"),
            canonical.decode("utf-8"), digest,
        ),
    )
    existing = conn.execute(
        "SELECT failure_sha256 FROM hard_edge_failure_events WHERE failure_id=%s",
        (event.failure_id,),
    ).fetchone()
    if not existing or str(existing[0]) != digest:
        raise RuntimeError("failure event collision or non-deterministic recomputation")
    return event.failure_id


def failure_counts(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    stage: str | None = None,
) -> list[dict[str, Any]]:
    """Deterministic count view grouped by stage, disposition and reason."""
    params: list[Any] = [session_id]
    where = "session_id=%s"
    if stage is not None:
        if stage not in VALID_STAGES:
            raise ValueError("unsupported failure event stage")
        where += " AND stage=%s"
        params.append(stage)
    rows = conn.execute(
        f"""
        SELECT stage,disposition_class,reason_code,count(*)
        FROM hard_edge_failure_events
        WHERE {where}
        GROUP BY stage,disposition_class,reason_code
        ORDER BY stage,disposition_class,reason_code
        """,
        tuple(params),
    ).fetchall()
    return [
        {
            "stage": str(row[0]),
            "disposition_class": str(row[1]),
            "reason_code": str(row[2]),
            "count": int(row[3]),
        }
        for row in rows
    ]
