from __future__ import annotations

"""Step 4H-D deterministic settlement auditor.

The auditor never changes historical hard state, eliminations, orders or source
records. It derives immutable pass/discrepancy/invariant-failure rows from
canonical facts after settlement/validation information exists.
"""

from datetime import date
import json
from typing import Any

import psycopg

from hard_information_domain import BucketElimination, HardClimateState
from market_calendar import event_trade_date
from settlement_audit_domain import (
    BenchmarkTradeProof,
    ExchangeMarketSettlement,
    build_exchange_market_settlement,
)
from settlement_journal import (
    SETTLEMENT_AUDITOR_VERSION,
    SettlementAuditResult,
    persist_exchange_market_settlement,
    persist_settlement_audit_result,
)
from settlement_validation import AuthoritativeSettlement, ValidationAuthority, ValidationProduct
from validation_collector import SettledEventCapture

AUDITOR_VERSION = SETTLEMENT_AUDITOR_VERSION


def benchmark_trade_proof_from_order(
    conn: psycopg.Connection[Any],
    *,
    order_id: int,
) -> BenchmarkTradeProof:
    row = conn.execute(
        """
        SELECT o.session_id,o.market_ticker,o.outcome_side,o.audit,
               s.event_ticker,s.station_code
        FROM paper_orders o
        JOIN paper_signals s ON s.id=o.signal_id
        WHERE o.id=%s
        """,
        (int(order_id),),
    ).fetchone()
    if not row:
        raise ValueError("paper order not found")
    session_id, market_ticker, outcome_side, audit_raw, event_ticker, station_code = row
    audit = _mapping(audit_raw)
    elimination_raw = audit.get("bucket_elimination")
    hard_state_raw = audit.get("hard_climate_state")
    rules_hash = str(audit.get("event_rules_hash") or "")
    if not isinstance(elimination_raw, dict):
        raise ValueError("paper order missing canonical bucket elimination")
    if not isinstance(hard_state_raw, dict):
        raise ValueError("paper order missing canonical hard state")
    elimination = BucketElimination.from_dict(elimination_raw)
    hard_state = HardClimateState.from_dict(hard_state_raw)
    if elimination.event_ticker != str(event_ticker or ""):
        raise ValueError("paper order event does not match elimination")
    if elimination.market_ticker != str(market_ticker or ""):
        raise ValueError("paper order market does not match elimination")
    if elimination.station_code != str(station_code or ""):
        raise ValueError("paper order station does not match elimination")
    return BenchmarkTradeProof(
        session_id=str(session_id),
        order_id=int(order_id),
        outcome_side=str(outcome_side),
        event_rules_hash=rules_hash,
        hard_state=hard_state,
        elimination=elimination,
    )


def normalize_exchange_capture_for_trade(
    conn: psycopg.Connection[Any],
    *,
    trade: BenchmarkTradeProof,
    capture: SettledEventCapture,
) -> ExchangeMarketSettlement:
    if not capture.fully_resolved or capture.fail_closed_reason is not None:
        raise ValueError("exchange event capture is not fully resolved")
    if capture.event_ticker != trade.event_ticker:
        raise ValueError("exchange capture event does not match trade")
    if capture.station_code != trade.station_code:
        raise ValueError("exchange capture station does not match trade")

    raw = conn.execute(
        """
        SELECT session_id,station_code,received_at,payload_sha256
        FROM raw_source_journal
        WHERE id=%s
        """,
        (int(capture.raw_source_id),),
    ).fetchone()
    if not raw:
        raise ValueError("exchange raw source not found")
    raw_session, raw_station, received_at, raw_hash = raw
    if str(raw_session) != trade.session_id:
        raise ValueError("exchange raw source belongs to different session")
    if raw_station is not None and str(raw_station).upper() != trade.station_code.upper():
        raise ValueError("exchange raw source station mismatch")
    if str(raw_hash) != capture.payload_sha256:
        raise ValueError("exchange capture payload hash mismatch")

    rule = conn.execute(
        """
        SELECT rules_hash,settlement_sources
        FROM settlement_rule_snapshots
        WHERE session_id=%s AND event_ticker=%s AND rules_hash=%s
        ORDER BY captured_at DESC,id DESC
        LIMIT 1
        """,
        (trade.session_id, trade.event_ticker, trade.event_rules_hash),
    ).fetchone()
    if not rule:
        raise ValueError("exact event rule snapshot for trade not found")
    rules_hash, sources_raw = str(rule[0]), rule[1]
    source_name = _first_source_name(sources_raw)
    if not source_name:
        raise ValueError("event rule snapshot has no settlement source name")

    target_day = event_trade_date(trade.event_ticker)
    if target_day is None or target_day != trade.climate_date:
        raise ValueError("trade event date does not match climate date")
    return build_exchange_market_settlement(
        event_ticker=trade.event_ticker,
        station_code=trade.station_code,
        climate_date=target_day,
        source_record_id=f"raw_source_journal:{int(capture.raw_source_id)}",
        source_payload_sha256=capture.payload_sha256,
        rules_hash=rules_hash,
        rule_source_name=source_name,
        captured_at=received_at,
        market_results=capture.market_results,
    )


def audit_exchange_market_result(
    *,
    trade: BenchmarkTradeProof,
    settlement: ExchangeMarketSettlement,
) -> SettlementAuditResult:
    _require_trade_settlement_identity(trade, settlement)
    result = settlement.result_for(trade.market_ticker)
    if result is None:
        raise ValueError("settled event does not contain traded market")
    if result == "yes":
        severity = "critical"
        status = "invariant_failure"
        code = "IMPOSSIBLE_BUCKET_SETTLED_YES"
    elif result == "no":
        severity = "info"
        status = "pass"
        code = "IMPOSSIBLE_BUCKET_SETTLED_NO"
    else:  # defensive; ExchangeMarketSettlement already rejects other values.
        raise ValueError("unsupported exchange market result")

    return SettlementAuditResult(
        session_id=trade.session_id,
        exchange_settlement_id=settlement.exchange_settlement_id,
        severity=severity,
        status=status,
        finding_code=code,
        station_code=trade.station_code,
        climate_date=trade.climate_date,
        state_id=trade.hard_state.state_id,
        elimination_id=trade.elimination.elimination_id,
        order_id=trade.order_id,
        market_ticker=trade.market_ticker,
        auditor_version=AUDITOR_VERSION,
        details={
            "event_ticker": trade.event_ticker,
            "market_result": result,
            "outcome_side": trade.outcome_side,
            "rules_hash": trade.event_rules_hash,
            "rule_source_name": settlement.rule_source_name,
            "exchange_raw_source_id": settlement.source_record_id,
            "exchange_payload_sha256": settlement.source_payload_sha256,
            "hard_lower_bound_f": trade.hard_state.proven_daily_high_min_f,
            "transition_evidence_id": trade.hard_state.transition_evidence_id,
            "supporting_evidence_ids": list(trade.hard_state.supporting_evidence_ids),
            "strike_rule": trade.elimination.strike_rule,
            "elimination_reason": trade.elimination.reason,
            "elimination_model_version": trade.elimination.elimination_model_version,
            "hard_state_model_version": trade.hard_state.state_model_version,
        },
    )


def audit_hard_state_against_final_max(
    *,
    trade: BenchmarkTradeProof,
    settlement: AuthoritativeSettlement,
) -> SettlementAuditResult:
    _require_numeric_truth_identity(trade, settlement)
    final_max = settlement.truth.final_max_f
    if final_max is None:
        raise ValueError("authoritative settlement has no numeric final max")
    if final_max < trade.hard_state.proven_daily_high_min_f:
        severity = "critical"
        status = "invariant_failure"
        code = "HARD_STATE_EXCEEDS_FINAL_MAX"
    else:
        severity = "info"
        status = "pass"
        code = "HARD_STATE_CONFIRMED_BY_FINAL_MAX"
    return SettlementAuditResult(
        session_id=trade.session_id,
        settlement_id=settlement.settlement_id,
        severity=severity,
        status=status,
        finding_code=code,
        station_code=trade.station_code,
        climate_date=trade.climate_date,
        state_id=trade.hard_state.state_id,
        elimination_id=trade.elimination.elimination_id,
        order_id=trade.order_id,
        market_ticker=trade.market_ticker,
        auditor_version=AUDITOR_VERSION,
        details={
            "event_ticker": trade.event_ticker,
            "final_max_f": final_max,
            "hard_lower_bound_f": trade.hard_state.proven_daily_high_min_f,
            "truth_id": settlement.truth.truth_id,
            "truth_source": settlement.truth.source,
            "truth_raw_source_id": settlement.truth.source_record_id,
            "rules_hash": settlement.rules_hash,
            "rule_source_name": settlement.rule_source_name,
            "transition_evidence_id": trade.hard_state.transition_evidence_id,
            "supporting_evidence_ids": list(trade.hard_state.supporting_evidence_ids),
        },
    )


def audit_validation_against_final_max(
    *,
    session_id: str,
    validation: ValidationProduct,
    settlement: AuthoritativeSettlement,
) -> SettlementAuditResult:
    if validation.authority is not ValidationAuthority.CORROBORATION_ONLY:
        raise ValueError("validation comparison expects corroboration-only product")
    if not validation.accepted_validation:
        raise ValueError("validation product is not usable for comparison")
    final_max = settlement.truth.final_max_f
    if final_max is None:
        raise ValueError("authoritative settlement has no numeric final max")
    if validation.climate_date != settlement.truth.climate_date:
        raise ValueError("validation/settlement climate-date mismatch")
    if validation.station_code != settlement.truth.station_code:
        raise ValueError("validation/settlement station mismatch")
    if validation.reported_max_f != final_max:
        severity = "warning"
        status = "discrepancy"
        code = "NON_AUTHORITATIVE_VALIDATION_DISAGREEMENT"
    else:
        severity = "info"
        status = "pass"
        code = "NON_AUTHORITATIVE_VALIDATION_AGREES"
    return SettlementAuditResult(
        session_id=session_id,
        settlement_id=settlement.settlement_id,
        validation_id=validation.validation_id,
        severity=severity,
        status=status,
        finding_code=code,
        station_code=validation.station_code,
        climate_date=validation.climate_date,
        auditor_version=AUDITOR_VERSION,
        details={
            "validation_source": validation.source,
            "validation_lifecycle": validation.lifecycle.value,
            "validation_max_f": validation.reported_max_f,
            "validation_raw_source_id": validation.source_record_id,
            "validation_payload_sha256": validation.source_payload_sha256,
            "authoritative_final_max_f": final_max,
            "truth_id": settlement.truth.truth_id,
            "truth_source": settlement.truth.source,
            "rule_source_name": settlement.rule_source_name,
            "classified_as_contract_invariant": False,
        },
    )


def audit_and_persist_trade_against_exchange_capture(
    conn: psycopg.Connection[Any],
    *,
    order_id: int,
    capture: SettledEventCapture,
) -> SettlementAuditResult:
    trade = benchmark_trade_proof_from_order(conn, order_id=order_id)
    settlement = normalize_exchange_capture_for_trade(conn, trade=trade, capture=capture)
    persist_exchange_market_settlement(
        conn,
        session_id=trade.session_id,
        settlement=settlement,
        raw_source_id=capture.raw_source_id,
    )
    result = audit_exchange_market_result(trade=trade, settlement=settlement)
    persist_settlement_audit_result(conn, result=result)
    return result


def _require_trade_settlement_identity(
    trade: BenchmarkTradeProof,
    settlement: ExchangeMarketSettlement,
) -> None:
    if settlement.event_ticker != trade.event_ticker:
        raise ValueError("trade/exchange event mismatch")
    if settlement.station_code != trade.station_code:
        raise ValueError("trade/exchange station mismatch")
    if settlement.climate_date != trade.climate_date:
        raise ValueError("trade/exchange climate-date mismatch")
    if settlement.rules_hash != trade.event_rules_hash:
        raise ValueError("trade/exchange rule-snapshot mismatch")


def _require_numeric_truth_identity(
    trade: BenchmarkTradeProof,
    settlement: AuthoritativeSettlement,
) -> None:
    if settlement.event_ticker != trade.event_ticker:
        raise ValueError("trade/settlement event mismatch")
    if settlement.truth.station_code != trade.station_code:
        raise ValueError("trade/settlement station mismatch")
    if settlement.truth.climate_date != trade.climate_date:
        raise ValueError("trade/settlement climate-date mismatch")
    if settlement.rules_hash != trade.event_rules_hash:
        raise ValueError("trade/settlement rule-snapshot mismatch")


def _first_source_name(value: Any) -> str:
    items = value
    if isinstance(value, str):
        try:
            items = json.loads(value)
        except json.JSONDecodeError:
            return ""
    if not isinstance(items, list):
        return ""
    for item in items:
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            return str(item["name"]).strip()
    return ""


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise ValueError("expected JSON object")
