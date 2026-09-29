from __future__ import annotations

"""Step 4I-A canonical hard-edge explanation and raw inspection.

This module is deliberately source-neutral. It never parses METAR, MADIS, DSM
or CLI syntax. It reconstructs why a benchmark order existed only from persisted
canonical objects and their immutable provenance links.
"""

import base64
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Mapping

import psycopg

from hard_information_domain import BucketElimination, HardClimateState
from raw_journal import canonical_json_bytes, sha256_hex

EXPLAINABILITY_VERSION = "hard-edge-explain-v1"


def explain_order(
    conn: psycopg.Connection[Any],
    *,
    order_id: int,
) -> dict[str, Any]:
    """Return one deterministic authoritative explanation for a benchmark order.

    Missing or inconsistent canonical links fail closed. Raw bytes are referenced
    by id/hash here and can be retrieved exactly with :func:`inspect_raw_source`.
    """
    row = conn.execute(
        """
        SELECT o.id,o.session_id,o.signal_id,o.market_ticker,o.outcome_side,
               o.latency_profile_ms,o.book_seq,o.status,o.avg_fill_price,
               o.filled_qty,o.gross_cost,o.estimated_fee,o.simulated_arrival_at,
               o.execution_model_version,o.book_snapshot,o.audit,
               s.event_ticker,s.station_code,s.triggered_at,s.strategy_code,s.evidence
        FROM paper_orders o
        JOIN paper_signals s ON s.id=o.signal_id
        WHERE o.id=%s
        """,
        (int(order_id),),
    ).fetchone()
    if not row:
        raise ValueError("paper order not found")

    (
        db_order_id, session_id, signal_id, market_ticker, outcome_side,
        latency_profile_ms, book_seq, order_status, avg_fill_price,
        filled_qty, gross_cost, estimated_fee, simulated_arrival_at,
        execution_model_version, book_snapshot_raw, audit_raw,
        event_ticker, station_code, triggered_at, strategy_code, signal_evidence_raw,
    ) = row

    audit = _mapping(audit_raw)
    signal_evidence = _mapping(signal_evidence_raw)
    hard_state_raw = audit.get("hard_climate_state") or signal_evidence.get("hard_climate_state")
    elimination_raw = audit.get("bucket_elimination") or signal_evidence.get("bucket_elimination")
    context = _mapping(audit.get("elimination_context") or signal_evidence.get("elimination_context"))
    if not isinstance(hard_state_raw, dict):
        raise ValueError("order missing canonical hard state")
    if not isinstance(elimination_raw, dict):
        raise ValueError("order missing canonical bucket elimination")

    hard_state = HardClimateState.from_dict(hard_state_raw)
    elimination = BucketElimination.from_dict(elimination_raw)
    event = str(event_ticker or "")
    market = str(market_ticker or "")
    station = str(station_code or "")
    if not event or not market or not station:
        raise ValueError("order/signal identity is incomplete")
    if elimination.event_ticker != event:
        raise ValueError("order event does not match elimination")
    if elimination.market_ticker != market:
        raise ValueError("order market does not match elimination")
    if elimination.station_code != station:
        raise ValueError("order station does not match elimination")
    if hard_state.station_code != station:
        raise ValueError("order station does not match hard state")
    if elimination.hard_state_id != hard_state.state_id:
        raise ValueError("elimination does not match hard-state id")
    if elimination.climate_date != hard_state.climate_date:
        raise ValueError("elimination climate date does not match hard state")
    if context and market not in {str(v) for v in context.get("dead_market_tickers", [])}:
        raise ValueError("traded market is absent from canonical dead-market set")

    transition = _load_transition(
        conn,
        session_id=str(session_id),
        hard_state=hard_state,
    )
    evidence = [
        _load_evidence_trace(conn, evidence_id=evidence_id, session_id=str(session_id), station_code=station)
        for evidence_id in hard_state.supporting_evidence_ids
    ]
    if not evidence:
        raise ValueError("hard state has no supporting evidence")
    if hard_state.transition_evidence_id not in {item["evidence_id"] for item in evidence}:
        raise ValueError("transition evidence missing from supporting evidence trace")

    raw_sources: dict[int, dict[str, Any]] = {}
    for item in evidence:
        for raw in item["raw_sources"]:
            raw_sources[int(raw["raw_source_id"])] = raw

    settlement_audits = _load_settlement_audits(conn, order_id=int(db_order_id))
    payload: dict[str, Any] = {
        "explainability_version": EXPLAINABILITY_VERSION,
        "order": {
            "order_id": int(db_order_id),
            "session_id": str(session_id),
            "signal_id": int(signal_id),
            "strategy_code": str(strategy_code),
            "event_ticker": event,
            "market_ticker": market,
            "outcome_side": str(outcome_side),
            "status": str(order_status),
            "triggered_at": triggered_at,
            "simulated_arrival_at": simulated_arrival_at,
            "latency_profile_ms": int(latency_profile_ms),
            "book_seq": int(book_seq) if book_seq is not None else None,
            "avg_fill_price": avg_fill_price,
            "filled_qty": filled_qty,
            "gross_cost": gross_cost,
            "estimated_fee": estimated_fee,
            "execution_model_version": str(execution_model_version or ""),
            "book_snapshot": _mapping(book_snapshot_raw),
        },
        "hard_state": hard_state.to_dict(),
        "transition": transition,
        "elimination": elimination.to_dict(),
        "newly_dead_market_tickers": [str(v) for v in context.get("dead_market_tickers", [])],
        "evidence": evidence,
        "raw_sources": [raw_sources[key] for key in sorted(raw_sources)],
        "settlement_audits": settlement_audits,
    }
    canonical = canonical_json_bytes(payload)
    payload["trace_sha256"] = sha256(canonical).hexdigest()
    return payload


def inspect_raw_source(
    conn: psycopg.Connection[Any],
    *,
    raw_source_id: int,
) -> dict[str, Any]:
    """Return the exact immutable raw bytes plus verified hash in JSON-safe form."""
    row = conn.execute(
        """
        SELECT id,capture_id,session_id,source,source_stream,station_code,
               observed_at,source_published_at,first_fetchable_at,received_at,
               transport,sequence_key,content_type,content_encoding,
               raw_bytes,payload_sha256,metadata
        FROM raw_source_journal
        WHERE id=%s
        """,
        (int(raw_source_id),),
    ).fetchone()
    if not row:
        raise ValueError("immutable raw source not found")
    (
        row_id, capture_id, session_id, source, source_stream, station_code,
        observed_at, source_published_at, first_fetchable_at, received_at,
        transport, sequence_key, content_type, content_encoding,
        raw_bytes, payload_sha256, metadata_raw,
    ) = row
    payload = bytes(raw_bytes)
    actual = sha256_hex(payload)
    expected = str(payload_sha256)
    if actual != expected:
        raise RuntimeError("immutable raw-source hash mismatch")
    utf8_text: str | None = None
    try:
        candidate = payload.decode("utf-8")
        if candidate.encode("utf-8") == payload:
            utf8_text = candidate
    except UnicodeDecodeError:
        pass
    return {
        "raw_source_id": int(row_id),
        "capture_id": str(capture_id),
        "session_id": str(session_id),
        "source": str(source),
        "source_stream": str(source_stream),
        "station_code": str(station_code) if station_code is not None else None,
        "observed_at": observed_at,
        "source_published_at": source_published_at,
        "first_fetchable_at": first_fetchable_at,
        "received_at": received_at,
        "transport": str(transport),
        "sequence_key": str(sequence_key) if sequence_key is not None else None,
        "content_type": str(content_type),
        "content_encoding": str(content_encoding) if content_encoding is not None else None,
        "payload_sha256": expected,
        "raw_bytes_base64": base64.b64encode(payload).decode("ascii"),
        "utf8_text": utf8_text,
        "metadata": _mapping(metadata_raw),
    }


def canonical_explanation_bytes(value: Mapping[str, Any]) -> bytes:
    """Stable serialization used by replay/tests and external audit tooling."""
    return canonical_json_bytes(dict(value))


def _load_transition(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    hard_state: HardClimateState,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT state_id,station_code,climate_date,proven_daily_high_min_f,
               first_known_at,transition_evidence_id,supporting_evidence_ids,
               state_model_version,calendar_version,transition_sha256
        FROM hard_state_transitions
        WHERE state_id=%s AND session_id=%s
        """,
        (hard_state.state_id, session_id),
    ).fetchone()
    if not row:
        raise ValueError("canonical hard-state transition not found")
    (
        state_id, station_code, climate_day, bound, first_known_at,
        transition_evidence_id, supporting_raw, state_model_version,
        calendar_version, transition_sha256,
    ) = row
    supporting = tuple(str(v) for v in _sequence(supporting_raw))
    if str(state_id) != hard_state.state_id:
        raise ValueError("hard-state transition identity mismatch")
    if str(station_code) != hard_state.station_code:
        raise ValueError("hard-state transition station mismatch")
    if _date_value(climate_day) != hard_state.climate_date:
        raise ValueError("hard-state transition climate-date mismatch")
    if int(bound) != hard_state.proven_daily_high_min_f:
        raise ValueError("hard-state transition bound mismatch")
    if str(transition_evidence_id) != hard_state.transition_evidence_id:
        raise ValueError("hard-state transition evidence mismatch")
    if supporting != tuple(hard_state.supporting_evidence_ids):
        raise ValueError("hard-state supporting-evidence set mismatch")
    if str(state_model_version) != hard_state.state_model_version:
        raise ValueError("hard-state model version mismatch")
    if str(calendar_version) != hard_state.calendar_version:
        raise ValueError("hard-state calendar version mismatch")
    return {
        "state_id": str(state_id),
        "station_code": str(station_code),
        "climate_date": _date_value(climate_day),
        "new_proven_lower_bound_f": int(bound),
        "first_known_at": first_known_at,
        "transition_evidence_id": str(transition_evidence_id),
        "supporting_evidence_ids": list(supporting),
        "state_model_version": str(state_model_version),
        "calendar_version": str(calendar_version),
        "transition_sha256": str(transition_sha256),
    }


def _load_evidence_trace(
    conn: psycopg.Connection[Any],
    *,
    evidence_id: str,
    session_id: str,
    station_code: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT evidence_id,station_code,climate_date,evidence_type,trust,
               integrity_status,proven_min_f,proven_max_f,possible_canonical_f,
               raw_identifier,observed_at,source_published_at,first_fetchable_at,
               mercury_received_at,mercury_interpreted_at,parser_version,
               evidence_model_version,calendar_version,derivation_payload,
               derivation_sha256
        FROM evidence_derivations
        WHERE evidence_id=%s AND session_id=%s
        """,
        (evidence_id, session_id),
    ).fetchone()
    if not row:
        raise ValueError(f"canonical evidence derivation not found: {evidence_id}")
    (
        db_evidence_id, db_station, climate_day, evidence_type, trust,
        integrity_status, proven_min_f, proven_max_f, possible_raw,
        raw_identifier, observed_at, source_published_at, first_fetchable_at,
        mercury_received_at, mercury_interpreted_at, parser_version,
        evidence_model_version, calendar_version, derivation_payload_raw,
        derivation_sha256,
    ) = row
    if str(db_evidence_id) != evidence_id or str(db_station) != station_code:
        raise ValueError("evidence identity/station mismatch")
    if str(trust) != "benchmark_eligible":
        raise ValueError("supporting evidence is not benchmark eligible")

    raw_rows = conn.execute(
        """
        SELECT l.ordinal,r.id,r.source,r.source_stream,r.station_code,
               r.observed_at,r.source_published_at,r.first_fetchable_at,
               r.received_at,r.payload_sha256,r.content_type,r.content_encoding
        FROM evidence_source_links l
        JOIN raw_source_journal r ON r.id=l.raw_source_id
        WHERE l.evidence_id=%s
        ORDER BY l.ordinal ASC,r.id ASC
        """,
        (evidence_id,),
    ).fetchall()
    if not raw_rows:
        raise ValueError("benchmark evidence has no immutable raw-source links")
    raw_sources: list[dict[str, Any]] = []
    for raw in raw_rows:
        (
            ordinal, raw_id, source, source_stream, raw_station,
            raw_observed_at, raw_published_at, raw_first_fetchable_at,
            raw_received_at, payload_sha256, content_type, content_encoding,
        ) = raw
        if raw_station is not None and str(raw_station).upper() != station_code.upper():
            raise ValueError("raw-source station does not match evidence")
        raw_sources.append({
            "ordinal": int(ordinal),
            "raw_source_id": int(raw_id),
            "source": str(source),
            "source_stream": str(source_stream),
            "station_code": str(raw_station) if raw_station is not None else None,
            "observed_at": raw_observed_at,
            "source_published_at": raw_published_at,
            "first_fetchable_at": raw_first_fetchable_at,
            "mercury_received_at": raw_received_at,
            "payload_sha256": str(payload_sha256),
            "content_type": str(content_type),
            "content_encoding": str(content_encoding) if content_encoding is not None else None,
        })

    possible = [int(v) for v in _sequence(possible_raw)]
    derivation_payload = _mapping(derivation_payload_raw)
    return {
        "evidence_id": evidence_id,
        "evidence_type": str(evidence_type),
        "source_types": sorted({item["source"] for item in raw_sources}),
        "station_code": str(db_station),
        "climate_date": _date_value(climate_day),
        "trust": str(trust),
        "integrity_status": str(integrity_status),
        "raw_identifier": str(raw_identifier) if raw_identifier is not None else None,
        "canonical_interpretation": {
            "possible_canonical_f": possible,
            "proven_min_f": int(proven_min_f) if proven_min_f is not None else None,
            "proven_max_f": int(proven_max_f) if proven_max_f is not None else None,
        },
        "clocks": {
            "observed_at": observed_at,
            "source_published_at": source_published_at,
            "first_fetchable_at": first_fetchable_at,
            "mercury_received_at": mercury_received_at,
            "mercury_interpreted_at": mercury_interpreted_at,
        },
        "versions": {
            "parser_version": str(parser_version),
            "evidence_model_version": str(evidence_model_version),
            "calendar_version": str(calendar_version),
        },
        "derivation_sha256": str(derivation_sha256),
        "derivation_payload": derivation_payload,
        "raw_sources": raw_sources,
    }


def _load_settlement_audits(conn: psycopg.Connection[Any], *, order_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT audit_id,severity,status,finding_code,settlement_id,validation_id,
               exchange_settlement_id,auditor_version,details,audit_sha256,created_at
        FROM settlement_audit_results
        WHERE order_id=%s
        ORDER BY created_at ASC,audit_id ASC
        """,
        (int(order_id),),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        (
            audit_id, severity, status, finding_code, settlement_id, validation_id,
            exchange_settlement_id, auditor_version, details_raw, audit_sha256, created_at,
        ) = row
        result.append({
            "audit_id": str(audit_id),
            "severity": str(severity),
            "status": str(status),
            "finding_code": str(finding_code),
            "settlement_id": str(settlement_id) if settlement_id is not None else None,
            "validation_id": str(validation_id) if validation_id is not None else None,
            "exchange_settlement_id": str(exchange_settlement_id) if exchange_settlement_id is not None else None,
            "auditor_version": str(auditor_version),
            "details": _mapping(details_raw),
            "audit_sha256": str(audit_sha256),
            "created_at": created_at,
        })
    return result


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
    try:
        return dict(value)
    except Exception as exc:  # pragma: no cover - driver-specific defensive path
        raise ValueError("expected mapping") from exc


def _sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if not isinstance(decoded, list):
            raise ValueError("expected JSON array")
        return decoded
    try:
        return list(value)
    except Exception as exc:  # pragma: no cover
        raise ValueError("expected sequence") from exc


def _date_value(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))
