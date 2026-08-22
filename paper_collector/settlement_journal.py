from __future__ import annotations

"""Append-only Step 4H validation, settlement and audit persistence."""

from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from typing import Any, Mapping

import psycopg

from raw_journal import canonical_json_bytes, sha256_hex
from settlement_validation import AuthoritativeSettlement, ValidationProduct

SETTLEMENT_JOURNAL_VERSION = "settlement-journal-v1"
SETTLEMENT_AUDITOR_VERSION = "settlement-auditor-v1"


@dataclass(frozen=True)
class SettlementAuditResult:
    session_id: str
    severity: str
    status: str
    finding_code: str
    station_code: str
    climate_date: date
    settlement_id: str | None = None
    validation_id: str | None = None
    state_id: str | None = None
    elimination_id: str | None = None
    order_id: int | None = None
    market_ticker: str | None = None
    auditor_version: str = SETTLEMENT_AUDITOR_VERSION
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in {"info", "warning", "critical"}:
            raise ValueError("unsupported settlement audit severity")
        if self.status not in {"pass", "discrepancy", "invariant_failure"}:
            raise ValueError("unsupported settlement audit status")
        if not self.settlement_id and not self.validation_id:
            raise ValueError("settlement audit requires settlement_id or validation_id")
        if not self.finding_code.strip():
            raise ValueError("settlement audit finding_code is required")
        if not self.station_code.strip():
            raise ValueError("settlement audit station_code is required")

    @property
    def audit_id(self) -> str:
        identity = "|".join((
            SETTLEMENT_JOURNAL_VERSION,
            self.auditor_version,
            self.session_id,
            self.settlement_id or "",
            self.validation_id or "",
            self.finding_code,
            self.station_code,
            self.climate_date.isoformat(),
            self.state_id or "",
            self.elimination_id or "",
            str(self.order_id) if self.order_id is not None else "",
            self.market_ticker or "",
        )).encode("utf-8")
        return f"settlement-audit:{sha256(identity).hexdigest()[:40]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "session_id": self.session_id,
            "severity": self.severity,
            "status": self.status,
            "finding_code": self.finding_code,
            "station_code": self.station_code,
            "climate_date": self.climate_date.isoformat(),
            "settlement_id": self.settlement_id,
            "validation_id": self.validation_id,
            "state_id": self.state_id,
            "elimination_id": self.elimination_id,
            "order_id": self.order_id,
            "market_ticker": self.market_ticker,
            "auditor_version": self.auditor_version,
            "details": dict(self.details),
        }


def persist_validation_product(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    product: ValidationProduct,
    raw_source_id: int,
) -> str:
    raw = _verify_raw_source(
        conn,
        session_id=session_id,
        raw_source_id=raw_source_id,
        expected_record_id=product.source_record_id,
        station_code=product.station_code,
        expected_payload_sha256=product.source_payload_sha256,
    )
    payload = {
        "journal_version": SETTLEMENT_JOURNAL_VERSION,
        "raw_source_payload_sha256": raw[2],
        "validation_product": product.to_dict(),
    }
    canonical = canonical_json_bytes(payload)
    digest = sha256_hex(canonical)
    conn.execute(
        """
        INSERT INTO validation_products(
          validation_id,session_id,source,source_product_id,station_code,
          climate_date,reported_max_f,max_observed_at,issued_at,mercury_received_at,
          raw_source_id,source_payload_sha256,lifecycle,authority,corrected,
          revision_of,parser_version,validation_model_version,calendar_version,
          fail_closed_reason,product_payload,product_sha256
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
        )
        ON CONFLICT (validation_id) DO NOTHING
        """,
        (
            product.validation_id,
            session_id,
            product.source,
            product.source_product_id,
            product.station_code,
            product.climate_date,
            product.reported_max_f,
            product.max_observed_at,
            product.issued_at,
            product.mercury_received_at,
            raw_source_id,
            product.source_payload_sha256,
            product.lifecycle.value,
            product.authority.value,
            product.corrected,
            product.revision_of,
            product.parser_version,
            product.validation_model_version,
            product.calendar_version,
            product.fail_closed_reason,
            canonical.decode("utf-8"),
            digest,
        ),
    )
    existing = conn.execute(
        "SELECT product_sha256 FROM validation_products WHERE validation_id=%s",
        (product.validation_id,),
    ).fetchone()
    if not existing or str(existing[0]) != digest:
        raise RuntimeError("validation product collision or non-deterministic recomputation")
    return product.validation_id


def persist_authoritative_settlement(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    settlement: AuthoritativeSettlement,
    raw_source_id: int,
) -> str:
    truth = settlement.truth
    raw = _verify_raw_source(
        conn,
        session_id=session_id,
        raw_source_id=raw_source_id,
        expected_record_id=truth.source_record_id,
        station_code=truth.station_code,
        expected_payload_sha256=None,
    )
    payload = {
        "journal_version": SETTLEMENT_JOURNAL_VERSION,
        "raw_source_payload_sha256": raw[2],
        "authoritative_settlement": settlement.to_dict(),
    }
    canonical = canonical_json_bytes(payload)
    digest = sha256_hex(canonical)
    conn.execute(
        """
        INSERT INTO authoritative_settlements(
          settlement_id,truth_id,session_id,event_ticker,station_code,climate_date,
          final_max_f,source,raw_source_id,rules_hash,rule_source_name,
          settlement_source_name,authority,observed_or_issued_at,
          revision_of_truth_id,truth_model_version,parser_version,
          settlement_payload,settlement_sha256
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
        )
        ON CONFLICT (settlement_id) DO NOTHING
        """,
        (
            settlement.settlement_id,
            truth.truth_id,
            session_id,
            settlement.event_ticker,
            truth.station_code,
            truth.climate_date,
            truth.final_max_f,
            truth.source,
            raw_source_id,
            settlement.rules_hash,
            settlement.rule_source_name,
            settlement.settlement_source_name,
            settlement.authority.value,
            truth.observed_or_issued_at,
            truth.revision_of,
            truth.truth_model_version,
            settlement.parser_version,
            canonical.decode("utf-8"),
            digest,
        ),
    )
    existing = conn.execute(
        "SELECT settlement_sha256 FROM authoritative_settlements WHERE settlement_id=%s",
        (settlement.settlement_id,),
    ).fetchone()
    if not existing or str(existing[0]) != digest:
        raise RuntimeError("authoritative settlement collision or non-deterministic recomputation")
    return settlement.settlement_id


def persist_settlement_audit_result(
    conn: psycopg.Connection[Any],
    *,
    result: SettlementAuditResult,
) -> str:
    payload = {
        "journal_version": SETTLEMENT_JOURNAL_VERSION,
        "audit_result": result.to_dict(),
    }
    canonical = canonical_json_bytes(payload)
    digest = sha256_hex(canonical)
    conn.execute(
        """
        INSERT INTO settlement_audit_results(
          audit_id,session_id,settlement_id,validation_id,severity,status,
          finding_code,station_code,climate_date,state_id,elimination_id,
          order_id,market_ticker,auditor_version,details,audit_payload,audit_sha256
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s
        )
        ON CONFLICT (audit_id) DO NOTHING
        """,
        (
            result.audit_id,
            result.session_id,
            result.settlement_id,
            result.validation_id,
            result.severity,
            result.status,
            result.finding_code,
            result.station_code,
            result.climate_date,
            result.state_id,
            result.elimination_id,
            result.order_id,
            result.market_ticker,
            result.auditor_version,
            canonical_json_bytes(dict(result.details)).decode("utf-8"),
            canonical.decode("utf-8"),
            digest,
        ),
    )
    existing = conn.execute(
        "SELECT audit_sha256 FROM settlement_audit_results WHERE audit_id=%s",
        (result.audit_id,),
    ).fetchone()
    if not existing or str(existing[0]) != digest:
        raise RuntimeError("settlement audit collision or non-deterministic recomputation")
    return result.audit_id


def _verify_raw_source(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    raw_source_id: int,
    expected_record_id: str,
    station_code: str,
    expected_payload_sha256: str | None,
) -> tuple[str, str | None, str]:
    if expected_record_id != f"raw_source_journal:{int(raw_source_id)}":
        raise ValueError("canonical source_record_id does not match raw_source_id")
    row = conn.execute(
        "SELECT session_id,station_code,payload_sha256 FROM raw_source_journal WHERE id=%s",
        (int(raw_source_id),),
    ).fetchone()
    if not row:
        raise ValueError("immutable raw source does not exist")
    raw_session, raw_station, raw_hash = str(row[0]), row[1], str(row[2])
    if raw_session != session_id:
        raise ValueError("raw source belongs to a different session")
    if raw_station is not None and str(raw_station).upper() != station_code.upper():
        raise ValueError("raw source station does not match canonical product")
    if expected_payload_sha256 is not None and raw_hash != expected_payload_sha256:
        raise ValueError("raw source payload hash does not match canonical product")
    return raw_session, (str(raw_station) if raw_station is not None else None), raw_hash
