from __future__ import annotations

"""Step 4J-D settlement grading and immutable replay-result persistence.

A replay is a derivation over immutable source data. Settlement is allowed to
*grade* the replay only after the execution decisions already exist; it never
feeds back into hard state, elimination, L2 reconstruction, or order selection.

This module also emits a replay-native Step-4I explanation trace. The trace
follows reconstructed state -> supporting evidence -> immutable raw bytes,
plus the exact rule snapshot, causal L2 snapshot, and later exchange settlement.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

import psycopg

from explainability import EXPLAINABILITY_VERSION
from raw_journal import canonical_json_bytes
from replay_domain import (
    CURRENT_BENCHMARK_VERSIONS,
    ReplayFilter,
    ReplayManifest,
    ReplayPolicy,
    ReplayVersionBundle,
    build_manifest,
    load_replay_events,
)
from replay_execution import (
    ReplayDecision,
    ReplayExecutionConfig,
    ReplayExecutionResult,
    execute_replay,
    load_market_rows,
)
from replay_hard_state import ReplayHardStateResult, ReplayTransitionElimination, reconstruct_from_database
from settlement_audit_domain import ExchangeMarketSettlement

REPLAY_SETTLEMENT_VERSION = "replay-settlement-v1"
REPLAY_RESULT_VERSION = "deterministic-replay-result-v1"
REPLAY_EXPLANATION_VERSION = "replay-hard-edge-explain-v1"


@dataclass(frozen=True)
class ReplayTradeSettlement:
    decision_id: str
    state_id: str
    elimination_id: str | None
    event_ticker: str
    market_ticker: str
    rules_hash: str
    exchange_settlement_id: str | None
    settlement_sha256: str | None
    exchange_result: str | None
    status: str
    finding_code: str
    filled_qty: Decimal
    total_cost: Decimal
    payout: Decimal
    realized_profit: Decimal | None
    realized_roi: Decimal | None
    settlement_captured_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "state_id": self.state_id,
            "elimination_id": self.elimination_id,
            "event_ticker": self.event_ticker,
            "market_ticker": self.market_ticker,
            "rules_hash": self.rules_hash,
            "exchange_settlement_id": self.exchange_settlement_id,
            "settlement_sha256": self.settlement_sha256,
            "exchange_result": self.exchange_result,
            "status": self.status,
            "finding_code": self.finding_code,
            "filled_qty": str(self.filled_qty),
            "total_cost": str(self.total_cost),
            "payout": str(self.payout),
            "realized_profit": str(self.realized_profit) if self.realized_profit is not None else None,
            "realized_roi": str(self.realized_roi) if self.realized_roi is not None else None,
            "settlement_captured_at": self.settlement_captured_at.isoformat() if self.settlement_captured_at else None,
        }


@dataclass(frozen=True)
class ReplaySettlementGrade:
    manifest_id: str
    source_input_sha256: str
    execution_output_sha256: str
    status: str
    trade_settlements: tuple[ReplayTradeSettlement, ...]
    unsettled_trade_count: int
    total_trade_cost: Decimal
    total_payout: Decimal
    post_entry_cash: Decimal
    settled_cash: Decimal | None
    realized_pnl: Decimal | None
    benchmark_pnl_basis: str = "realized_hold_to_exchange_settlement"
    replay_settlement_version: str = REPLAY_SETTLEMENT_VERSION

    @property
    def output_sha256(self) -> str:
        return sha256(canonical_json_bytes(self.to_dict(include_hash=False))).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {
            "manifest_id": self.manifest_id,
            "source_input_sha256": self.source_input_sha256,
            "execution_output_sha256": self.execution_output_sha256,
            "status": self.status,
            "trade_settlements": [item.to_dict() for item in self.trade_settlements],
            "unsettled_trade_count": self.unsettled_trade_count,
            "total_trade_cost": str(self.total_trade_cost),
            "total_payout": str(self.total_payout),
            "post_entry_cash": str(self.post_entry_cash),
            "settled_cash": str(self.settled_cash) if self.settled_cash is not None else None,
            "realized_pnl": str(self.realized_pnl) if self.realized_pnl is not None else None,
            "benchmark_pnl_basis": self.benchmark_pnl_basis,
            "replay_settlement_version": self.replay_settlement_version,
        }
        if include_hash:
            payload["output_sha256"] = self.output_sha256
        return payload


@dataclass(frozen=True)
class LoadedExchangeSettlement:
    settlement: ExchangeMarketSettlement
    settlement_sha256: str


@dataclass(frozen=True)
class ReplayRunResult:
    manifest: ReplayManifest
    hard_state: ReplayHardStateResult
    execution: ReplayExecutionResult
    settlement: ReplaySettlementGrade
    explanation: Mapping[str, Any]
    replay_result_id: str

    @property
    def canonical_output_sha256(self) -> str:
        return sha256(canonical_json_bytes({
            "manifest": self.manifest.to_dict(),
            "hard_state": self.hard_state.to_dict(),
            "execution": self.execution.to_dict(),
            "settlement": self.settlement.to_dict(),
            "explanation": dict(self.explanation),
            "replay_result_id": self.replay_result_id,
        })).hexdigest()


def load_exchange_settlements(
    conn: psycopg.Connection[Any],
    *,
    source_session_id: str,
    event_ticker: str,
    station_code: str,
    climate_date: date,
) -> tuple[LoadedExchangeSettlement, ...]:
    rows = conn.execute(
        """
        SELECT exchange_settlement_id,event_ticker,station_code,climate_date,
               rules_hash,captured_at,settlement_payload,settlement_sha256
          FROM exchange_market_settlements
         WHERE session_id=%s AND event_ticker=%s AND station_code=%s AND climate_date=%s
         ORDER BY captured_at ASC,exchange_settlement_id ASC
        """,
        (source_session_id, event_ticker, station_code, climate_date),
    ).fetchall()
    out: list[LoadedExchangeSettlement] = []
    for row in rows:
        payload = _mapping(row[6])
        digest = sha256(canonical_json_bytes(payload)).hexdigest()
        if digest != str(row[7]):
            raise RuntimeError("exchange settlement journal hash mismatch")
        core = payload.get("exchange_market_settlement")
        if not isinstance(core, Mapping):
            raise ValueError("exchange settlement payload missing canonical settlement")
        settlement = ExchangeMarketSettlement.from_dict(core)
        if settlement.exchange_settlement_id != str(row[0]):
            raise ValueError("exchange settlement id mismatch")
        if settlement.event_ticker != str(row[1]):
            raise ValueError("exchange settlement event mismatch")
        if settlement.station_code != str(row[2]):
            raise ValueError("exchange settlement station mismatch")
        if settlement.climate_date != _date(row[3]):
            raise ValueError("exchange settlement climate-date mismatch")
        if settlement.rules_hash != str(row[4]):
            raise ValueError("exchange settlement rules hash mismatch")
        if settlement.captured_at != _aware(row[5]):
            raise ValueError("exchange settlement captured-at mismatch")
        out.append(LoadedExchangeSettlement(settlement=settlement, settlement_sha256=digest))
    return tuple(out)


def grade_execution(
    *,
    manifest: ReplayManifest,
    hard_state: ReplayHardStateResult,
    execution: ReplayExecutionResult,
    config: ReplayExecutionConfig,
    settlements: Sequence[LoadedExchangeSettlement],
) -> ReplaySettlementGrade:
    if execution.manifest_id != manifest.manifest_id:
        raise ValueError("execution/manifest identity mismatch")
    if execution.hard_state_output_sha256 != hard_state.output_sha256:
        raise ValueError("execution/hard-state hash mismatch")
    if execution.execution_config_sha256 != config.config_sha256:
        raise ValueError("execution/config hash mismatch")

    trades = [item for item in execution.decisions if item.decision == "trade"]
    graded: list[ReplayTradeSettlement] = []
    unresolved = 0
    total_cost = Decimal(0)
    total_payout = Decimal(0)
    any_invariant_failure = False

    for decision in trades:
        if decision.filled_qty <= 0 or decision.total_cost <= 0:
            raise ValueError("trade decision has no positive fill/cost")
        elimination = _elimination_for_decision(hard_state, decision)
        rules_hash = str(elimination.rule_rules_hash or "")
        if len(rules_hash) != 64:
            raise ValueError("replay trade lacks exact rule-snapshot hash")

        identity_matches = [
            item for item in settlements
            if item.settlement.event_ticker == decision.event_ticker
            and item.settlement.station_code == hard_state.station_code
            and item.settlement.climate_date == hard_state.climate_date
        ]
        exact = [item for item in identity_matches if item.settlement.rules_hash == rules_hash]
        total_cost += decision.total_cost

        if not exact:
            unresolved += 1
            if identity_matches:
                any_invariant_failure = True
                code = "SETTLEMENT_RULE_SNAPSHOT_MISMATCH"
                status = "invariant_failure"
            else:
                code = "NO_AUTHORITATIVE_EXCHANGE_SETTLEMENT"
                status = "incomplete"
            graded.append(ReplayTradeSettlement(
                decision_id=decision.decision_id,
                state_id=decision.state_id,
                elimination_id=decision.elimination_id,
                event_ticker=decision.event_ticker,
                market_ticker=decision.market_ticker,
                rules_hash=rules_hash,
                exchange_settlement_id=None,
                settlement_sha256=None,
                exchange_result=None,
                status=status,
                finding_code=code,
                filled_qty=decision.filled_qty,
                total_cost=decision.total_cost,
                payout=Decimal(0),
                realized_profit=None,
                realized_roi=None,
                settlement_captured_at=None,
            ))
            continue

        selected = max(exact, key=lambda item: (item.settlement.captured_at, item.settlement.exchange_settlement_id))
        settlement = selected.settlement
        if settlement.captured_at < decision.simulated_arrival_at:
            any_invariant_failure = True
            unresolved += 1
            graded.append(ReplayTradeSettlement(
                decision_id=decision.decision_id,
                state_id=decision.state_id,
                elimination_id=decision.elimination_id,
                event_ticker=decision.event_ticker,
                market_ticker=decision.market_ticker,
                rules_hash=rules_hash,
                exchange_settlement_id=settlement.exchange_settlement_id,
                settlement_sha256=selected.settlement_sha256,
                exchange_result=None,
                status="invariant_failure",
                finding_code="SETTLEMENT_PRECEDES_REPLAY_DECISION",
                filled_qty=decision.filled_qty,
                total_cost=decision.total_cost,
                payout=Decimal(0),
                realized_profit=None,
                realized_roi=None,
                settlement_captured_at=settlement.captured_at,
            ))
            continue

        result = settlement.result_for(decision.market_ticker)
        if result is None:
            unresolved += 1
            graded.append(ReplayTradeSettlement(
                decision_id=decision.decision_id,
                state_id=decision.state_id,
                elimination_id=decision.elimination_id,
                event_ticker=decision.event_ticker,
                market_ticker=decision.market_ticker,
                rules_hash=rules_hash,
                exchange_settlement_id=settlement.exchange_settlement_id,
                settlement_sha256=selected.settlement_sha256,
                exchange_result=None,
                status="incomplete",
                finding_code="TRADED_MARKET_MISSING_FROM_SETTLEMENT",
                filled_qty=decision.filled_qty,
                total_cost=decision.total_cost,
                payout=Decimal(0),
                realized_profit=None,
                realized_roi=None,
                settlement_captured_at=settlement.captured_at,
            ))
            continue

        payout = decision.filled_qty if result == "no" else Decimal(0)
        profit = payout - decision.total_cost
        roi = profit / decision.total_cost
        total_payout += payout
        if result == "yes":
            any_invariant_failure = True
            status = "invariant_failure"
            code = "IMPOSSIBLE_BUCKET_SETTLED_YES"
        else:
            status = "pass"
            code = "IMPOSSIBLE_BUCKET_SETTLED_NO"
        graded.append(ReplayTradeSettlement(
            decision_id=decision.decision_id,
            state_id=decision.state_id,
            elimination_id=decision.elimination_id,
            event_ticker=decision.event_ticker,
            market_ticker=decision.market_ticker,
            rules_hash=rules_hash,
            exchange_settlement_id=settlement.exchange_settlement_id,
            settlement_sha256=selected.settlement_sha256,
            exchange_result=result,
            status=status,
            finding_code=code,
            filled_qty=decision.filled_qty,
            total_cost=decision.total_cost,
            payout=payout,
            realized_profit=profit,
            realized_roi=roi,
            settlement_captured_at=settlement.captured_at,
        ))

    if any_invariant_failure:
        overall = "invariant_failure"
    elif unresolved:
        overall = "incomplete"
    else:
        overall = "pass"

    settled_cash = None if unresolved else execution.ending_cash + total_payout
    realized_pnl = None if settled_cash is None else settled_cash - config.starting_cash
    return ReplaySettlementGrade(
        manifest_id=manifest.manifest_id,
        source_input_sha256=manifest.source_input_sha256,
        execution_output_sha256=execution.output_sha256,
        status=overall,
        trade_settlements=tuple(graded),
        unsettled_trade_count=unresolved,
        total_trade_cost=total_cost,
        total_payout=total_payout,
        post_entry_cash=execution.ending_cash,
        settled_cash=settled_cash,
        realized_pnl=realized_pnl,
    )


def build_replay_explanation(
    conn: psycopg.Connection[Any],
    *,
    hard_state: ReplayHardStateResult,
    execution: ReplayExecutionResult,
    settlement: ReplaySettlementGrade,
) -> dict[str, Any]:
    evidence_by_id = {item.evidence_id: item for item in hard_state.evidence}
    state_by_id = {item.state_id: item for item in hard_state.timeline.states}
    elimination_by_state = {item.state_id: item for item in hard_state.eliminations}
    settlement_by_decision = {item.decision_id: item for item in settlement.trade_settlements}

    traces: list[dict[str, Any]] = []
    for decision in execution.decisions:
        state = state_by_id.get(decision.state_id)
        elimination = elimination_by_state.get(decision.state_id)
        if state is None or elimination is None:
            raise ValueError("replay decision cannot be traced to canonical state/elimination")

        evidence_traces: list[dict[str, Any]] = []
        raw_sources: dict[int, dict[str, Any]] = {}
        for evidence_id in state.supporting_evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                raise ValueError("replay state supporting evidence is missing")
            raw_ids: list[int] = []
            for record_id in evidence.source_record_ids:
                prefix = "raw_source_journal:"
                if not record_id.startswith(prefix):
                    raise ValueError("replay explanation requires immutable raw-source identity")
                raw_id = int(record_id[len(prefix):])
                raw_ids.append(raw_id)
                raw_sources[raw_id] = _verified_raw_source(conn, raw_id)
            evidence_traces.append({
                "evidence_id": evidence.evidence_id,
                "evidence_type": evidence.evidence_type.value,
                "trust": evidence.trust.value,
                "integrity_status": evidence.integrity_status.value,
                "proven_min_f": evidence.proven_min_f,
                "proven_max_f": evidence.proven_max_f,
                "raw_identifier": evidence.raw_identifier,
                "source_record_ids": list(evidence.source_record_ids),
                "raw_source_ids": raw_ids,
                "clocks": evidence.clocks.to_dict(),
                "versions": {
                    "parser_version": evidence.parser_version,
                    "evidence_model_version": evidence.evidence_model_version,
                    "calendar_version": evidence.calendar_version,
                },
            })

        rule_trace = _rule_trace(conn, elimination)
        market_trace = _market_trace(conn, decision)
        settled = settlement_by_decision.get(decision.decision_id)
        settlement_trace = _settlement_trace(conn, settled) if settled and settled.exchange_settlement_id else None
        traces.append({
            "decision": decision.to_dict(),
            "hard_state": state.to_dict(),
            "elimination": elimination.to_dict(),
            "evidence": evidence_traces,
            "raw_sources": [raw_sources[key] for key in sorted(raw_sources)],
            "rule_snapshot": rule_trace,
            "market_snapshot": market_trace,
            "settlement": settled.to_dict() if settled else None,
            "settlement_source": settlement_trace,
        })

    payload: dict[str, Any] = {
        "explainability_version": EXPLAINABILITY_VERSION,
        "replay_explanation_version": REPLAY_EXPLANATION_VERSION,
        "manifest_id": execution.manifest_id,
        "hard_state_output_sha256": hard_state.output_sha256,
        "execution_output_sha256": execution.output_sha256,
        "settlement_output_sha256": settlement.output_sha256,
        "decision_traces": traces,
    }
    payload["trace_sha256"] = sha256(canonical_json_bytes(payload)).hexdigest()
    return payload


def persist_replay_result(
    conn: psycopg.Connection[Any],
    *,
    manifest: ReplayManifest,
    hard_state: ReplayHardStateResult,
    execution: ReplayExecutionResult,
    config: ReplayExecutionConfig,
    settlement: ReplaySettlementGrade,
    explanation: Mapping[str, Any],
) -> str:
    filt = manifest.replay_filter
    if not filt.station_code or not filt.climate_date or not filt.event_ticker:
        raise ValueError("persisted replay requires explicit station/date/event filter")

    versions = manifest.versions.to_dict()
    versions_sha = sha256(canonical_json_bytes(versions)).hexdigest()
    config_payload = config.to_dict()
    config_sha = config.config_sha256
    settlement_payload = settlement.to_dict()
    settlement_sha = settlement.output_sha256
    replay_payload = {
        "manifest": manifest.to_dict(),
        "hard_state": hard_state.to_dict(),
        "execution": execution.to_dict(),
        "settlement": settlement_payload,
        "explanation": dict(explanation),
    }
    replay_sha = sha256(canonical_json_bytes(replay_payload)).hexdigest()
    identity = {
        "source_session_id": manifest.source_session_id,
        "manifest_id": manifest.manifest_id,
        "policy": manifest.policy.value,
        "execution_config_sha256": config_sha,
        "settlement_grade_sha256": settlement_sha,
        "replay_model_version": REPLAY_RESULT_VERSION,
    }
    replay_result_id = "replay-result:" + sha256(canonical_json_bytes(identity)).hexdigest()[:40]

    conn.execute(
        """
        INSERT INTO deterministic_replay_results(
          replay_result_id,source_session_id,manifest_id,replay_policy,station_code,
          climate_date,event_ticker,source_input_sha256,version_bundle,
          version_bundle_sha256,execution_config,execution_config_sha256,
          hard_state_output_sha256,execution_output_sha256,settlement_grade,
          settlement_grade_sha256,replay_payload,replay_payload_sha256,replay_model_version
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s
        )
        ON CONFLICT (replay_result_id) DO NOTHING
        """,
        (
            replay_result_id, manifest.source_session_id, manifest.manifest_id,
            manifest.policy.value, filt.station_code, filt.climate_date,
            filt.event_ticker, manifest.source_input_sha256,
            canonical_json_bytes(versions).decode("utf-8"), versions_sha,
            canonical_json_bytes(config_payload).decode("utf-8"), config_sha,
            hard_state.output_sha256, execution.output_sha256,
            canonical_json_bytes(settlement_payload).decode("utf-8"), settlement_sha,
            canonical_json_bytes(replay_payload).decode("utf-8"), replay_sha,
            REPLAY_RESULT_VERSION,
        ),
    )
    existing = conn.execute(
        """
        SELECT replay_payload_sha256,settlement_grade_sha256
          FROM deterministic_replay_results
         WHERE replay_result_id=%s
        """,
        (replay_result_id,),
    ).fetchone()
    if not existing or str(existing[0]) != replay_sha or str(existing[1]) != settlement_sha:
        raise RuntimeError("replay-result collision or non-deterministic recomputation")
    return replay_result_id


def run_and_persist_replay(
    conn: psycopg.Connection[Any],
    *,
    source_session_id: str,
    replay_filter: ReplayFilter,
    execution_config: ReplayExecutionConfig,
    versions: ReplayVersionBundle = CURRENT_BENCHMARK_VERSIONS,
    policy: ReplayPolicy = ReplayPolicy.BENCHMARK,
) -> ReplayRunResult:
    events = load_replay_events(
        conn,
        source_session_id=source_session_id,
        replay_filter=replay_filter,
    )
    manifest = build_manifest(
        source_session_id=source_session_id,
        versions=versions,
        policy=policy,
        replay_filter=replay_filter,
        events=events,
    )
    hard_state = reconstruct_from_database(conn, manifest=manifest, events=events)
    execution = execute_replay(
        manifest=manifest,
        hard_state=hard_state,
        market_rows=load_market_rows(conn, session_id=source_session_id),
        config=execution_config,
    )
    if not replay_filter.event_ticker or not replay_filter.station_code or not replay_filter.climate_date:
        raise ValueError("4J-D replay requires exact event/station/climate-date filter")
    settlements = load_exchange_settlements(
        conn,
        source_session_id=source_session_id,
        event_ticker=replay_filter.event_ticker,
        station_code=replay_filter.station_code,
        climate_date=replay_filter.climate_date,
    )
    grade = grade_execution(
        manifest=manifest,
        hard_state=hard_state,
        execution=execution,
        config=execution_config,
        settlements=settlements,
    )
    explanation = build_replay_explanation(
        conn,
        hard_state=hard_state,
        execution=execution,
        settlement=grade,
    )
    result_id = persist_replay_result(
        conn,
        manifest=manifest,
        hard_state=hard_state,
        execution=execution,
        config=execution_config,
        settlement=grade,
        explanation=explanation,
    )
    return ReplayRunResult(
        manifest=manifest,
        hard_state=hard_state,
        execution=execution,
        settlement=grade,
        explanation=explanation,
        replay_result_id=result_id,
    )


def _elimination_for_decision(
    hard_state: ReplayHardStateResult,
    decision: ReplayDecision,
) -> ReplayTransitionElimination:
    matches = [item for item in hard_state.eliminations if item.state_id == decision.state_id]
    if len(matches) != 1:
        raise ValueError("replay decision has ambiguous/missing elimination transition")
    elimination = matches[0]
    payload = elimination.elimination_payload or {}
    candidates = [
        item for item in payload.get("eliminations", [])
        if isinstance(item, Mapping)
        and str(item.get("market_ticker") or "") == decision.market_ticker
        and item.get("eliminated") is True
    ]
    if len(candidates) != 1:
        raise ValueError("replay decision market is not uniquely eliminated")
    if decision.elimination_id and str(candidates[0].get("elimination_id") or "") != decision.elimination_id:
        raise ValueError("replay decision elimination id mismatch")
    return elimination


def _verified_raw_source(conn: psycopg.Connection[Any], raw_source_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id,source,source_stream,station_code,observed_at,received_at,
               transport,payload_sha256,raw_bytes
          FROM raw_source_journal
         WHERE id=%s
        """,
        (raw_source_id,),
    ).fetchone()
    if not row:
        raise ValueError("replay explanation raw source missing")
    raw_bytes = bytes(row[8])
    digest = sha256(raw_bytes).hexdigest()
    if digest != str(row[7]):
        raise RuntimeError("replay explanation raw-source hash mismatch")
    return {
        "raw_source_id": int(row[0]),
        "source": str(row[1]),
        "source_stream": str(row[2]),
        "station_code": str(row[3]) if row[3] is not None else None,
        "observed_at": _iso(row[4]),
        "received_at": _iso(row[5]),
        "transport": str(row[6]),
        "payload_sha256": digest,
    }


def _rule_trace(conn: psycopg.Connection[Any], elimination: ReplayTransitionElimination) -> dict[str, Any] | None:
    if elimination.rule_snapshot_id is None:
        return None
    row = conn.execute(
        """
        SELECT id,event_ticker,captured_at,rules_hash,raw_payload
          FROM settlement_rule_snapshots
         WHERE id=%s
        """,
        (elimination.rule_snapshot_id,),
    ).fetchone()
    if not row:
        raise ValueError("replay rule snapshot missing")
    if elimination.rule_rules_hash and str(row[3]) != elimination.rule_rules_hash:
        raise ValueError("replay rule snapshot hash mismatch")
    return {
        "rule_snapshot_id": int(row[0]),
        "event_ticker": str(row[1]),
        "captured_at": _aware(row[2]).isoformat(),
        "rules_hash": str(row[3]),
        "raw_payload_sha256": sha256(canonical_json_bytes(_mapping(row[4]))).hexdigest(),
    }


def _market_trace(conn: psycopg.Connection[Any], decision: ReplayDecision) -> dict[str, Any] | None:
    if decision.snapshot_id is None:
        return None
    row = conn.execute(
        """
        SELECT id,market_ticker,received_at,received_epoch_ms,received_epoch_ns,
               payload_sha256,raw_text,connection_id::text,seq
          FROM market_data_journal
         WHERE id=%s
        """,
        (decision.snapshot_id,),
    ).fetchone()
    if not row:
        raise ValueError("replay market snapshot missing")
    digest = sha256(str(row[6]).encode()).hexdigest()
    if digest != str(row[5]):
        raise RuntimeError("replay market snapshot hash mismatch")
    if str(row[1]) != decision.market_ticker:
        raise ValueError("replay market snapshot ticker mismatch")
    if decision.connection_id and str(row[7]) != decision.connection_id:
        raise ValueError("replay market snapshot connection mismatch")
    return {
        "market_data_journal_id": int(row[0]),
        "market_ticker": str(row[1]),
        "received_at": _aware(row[2]).isoformat(),
        "received_epoch_ms": int(row[3]),
        "received_epoch_ns": int(row[4]),
        "payload_sha256": digest,
        "connection_id": str(row[7]) if row[7] else None,
        "seq": int(row[8]) if row[8] is not None else None,
    }


def _settlement_trace(conn: psycopg.Connection[Any], item: ReplayTradeSettlement) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT exchange_settlement_id,captured_at,settlement_sha256,raw_source_id,
               rules_hash,event_ticker,station_code,climate_date
          FROM exchange_market_settlements
         WHERE exchange_settlement_id=%s
        """,
        (item.exchange_settlement_id,),
    ).fetchone()
    if not row:
        raise ValueError("replay settlement trace missing")
    if item.settlement_sha256 and str(row[2]) != item.settlement_sha256:
        raise ValueError("replay settlement trace hash mismatch")
    raw = _verified_raw_source(conn, int(row[3]))
    return {
        "exchange_settlement_id": str(row[0]),
        "captured_at": _aware(row[1]).isoformat(),
        "settlement_sha256": str(row[2]),
        "raw_source": raw,
        "rules_hash": str(row[4]),
        "event_ticker": str(row[5]),
        "station_code": str(row[6]),
        "climate_date": _date(row[7]).isoformat(),
    }


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError("expected JSON object")
        return decoded
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError("expected JSON object")


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return result


def _date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _iso(value: Any) -> str | None:
    return _aware(value).isoformat() if value is not None else None
