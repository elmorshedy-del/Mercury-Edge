from __future__ import annotations

"""Fail-closed wrapper around the original DBN paper engine.

The original implementation is retained for replay compatibility. Live paper
execution imports this module instead so DBN spends benchmark cash only when a
settlement-compatible canonical hard state establishes that a bucket is
impossible.

Step 4D makes the source-neutral accumulator authoritative for new immutable
captures. Step 4E makes pure bucket elimination authoritative downstream: raw
weather syntax and source identity are no longer involved once HardClimateState
exists.
"""

import json
from decimal import Decimal
from typing import Any

import psycopg

import bucket_elimination
import hard_state_proof
import paper_engine as base
import raw_journal
import risk_controls
from hard_information_domain import BucketElimination, HardClimateState
from hard_state_accumulator import HardStateTimeline, accumulate_hard_state
import hard_state_journal
from stations import STATIONS

# Re-export the primitives used by unified_engine / strategy runtime.
ensure_session_and_portfolios = base.ensure_session_and_portfolios
load_global = base.load_global
load_modes = base.load_modes
weather_row = base.weather_row
audit_error = base.audit_error
SESSION_ID = base.SESSION_ID

_ORIGINAL_MODE_BUDGET = base.mode_budget


def _risk_adjusted_mode_budget(
    conn: psycopg.Connection[Any],
    mode: dict[str, Any],
    candidate: base.Candidate,
    global_cfg: dict[str, Any],
):
    raw = _ORIGINAL_MODE_BUDGET(conn, mode, candidate, global_cfg)
    return risk_controls.apply_budget_multiplier(
        conn,
        int(mode["portfolio_id"]),
        raw,
        global_cfg,
    )


# base.execute_candidates resolves mode_budget from its module globals when it
# runs, so this applies the drawdown gate without forking the fill/fee engine.
base.mode_budget = _risk_adjusted_mode_budget


def _persist_canonical_evidence(
    conn: psycopg.Connection[Any],
    proof: hard_state_proof.HardStateProof,
    station: str,
) -> tuple[str, ...]:
    """Append every accepted versioned ASOS derivation with immutable provenance."""
    persisted: list[str] = []
    for record, evidence in zip(proof.evidence_records, proof.all_canonical_evidence(station)):
        if record.raw_source_id is None:
            continue
        persisted.append(raw_journal.persist_evidence_derivation(
            conn,
            session_id=base.SESSION_ID,
            evidence=evidence,
            raw_source_ids=(record.raw_source_id,),
        ))
    return tuple(persisted)


def _timeline_from_proof(
    proof: hard_state_proof.HardStateProof,
    station: str,
) -> HardStateTimeline | None:
    evidence = proof.all_canonical_evidence(station)
    if not evidence:
        return None
    return accumulate_hard_state(
        evidence,
        station_code=station,
        climate_date=proof.climate_trade_date,
    )


def _transition_record(
    proof: hard_state_proof.HardStateProof,
    station: str,
    state: HardClimateState,
) -> hard_state_proof.ProofRecord | None:
    for record, evidence in zip(proof.evidence_records, proof.all_canonical_evidence(station)):
        if evidence.evidence_id == state.transition_evidence_id:
            return record
    return None


def _current_weather_created_transition(
    proof: hard_state_proof.HardStateProof,
    station: str,
    state: HardClimateState,
    current_weather_id: int,
) -> bool:
    record = _transition_record(proof, station, state)
    if record is not None:
        return record.weather_id == int(current_weather_id)
    # Backward-compatible fixtures/historical rows created before the canonical
    # evidence adapter existed. New immutable captures always take the path above.
    return proof.is_new_transition(current_weather_id)


def _immutable_history_complete(proof: hard_state_proof.HardStateProof) -> bool:
    return bool(proof.evidence_records) and all(
        record.raw_source_id is not None for record in proof.evidence_records
    )


def _attach_proof_to_signal(
    conn: psycopg.Connection[Any],
    signal_id: int,
    proof: hard_state_proof.HardStateProof,
    state: HardClimateState,
    persisted_evidence_ids: tuple[str, ...] = (),
    timeline: HardStateTimeline | None = None,
    application_ids: tuple[str, ...] = (),
    transition_ids: tuple[str, ...] = (),
    elimination: BucketElimination | None = None,
    elimination_context: dict[str, Any] | None = None,
) -> None:
    """Persist raw provenance, canonical state, timeline, and elimination proof."""
    proof_payload = proof.as_dict()
    state_payload = state.to_dict()
    timeline_payload = timeline.to_dict() if timeline is not None else None
    elimination_payload = elimination.to_dict() if elimination is not None else None
    conn.execute(
        """
        UPDATE paper_signals
           SET evidence = evidence || jsonb_build_object(
                 'hard_state_proof', %s::jsonb,
                 'hard_climate_state', %s::jsonb,
                 'hard_state_timeline', %s::jsonb,
                 'proof_version', %s,
                 'hard_state_model_version', %s,
                 'proven_daily_high_min_f', %s,
                 'climate_trade_date', %s,
                 'trigger_evidence_grade', %s,
                 'trigger_raw_group', %s,
                 'immutable_provenance_complete', %s,
                 'evidence_derivation_ids', %s::jsonb,
                 'hard_state_application_ids', %s::jsonb,
                 'hard_state_transition_ids', %s::jsonb,
                 'bucket_elimination', %s::jsonb,
                 'elimination_context', %s::jsonb,
                 'elimination_model_version', %s
               )
         WHERE id=%s
        """,
        (
            json.dumps(proof_payload, separators=(",", ":"), default=base.json_default),
            json.dumps(state_payload, separators=(",", ":"), default=base.json_default),
            json.dumps(timeline_payload, separators=(",", ":"), default=base.json_default),
            hard_state_proof.PROOF_VERSION,
            state.state_model_version,
            state.proven_daily_high_min_f,
            state.climate_date.isoformat(),
            (_transition_record(proof, state.station_code, state).grade if _transition_record(proof, state.station_code, state) else proof.trigger_grade),
            (_transition_record(proof, state.station_code, state).raw_group if _transition_record(proof, state.station_code, state) else proof.trigger_raw_group),
            proof.immutable_provenance_complete,
            json.dumps(list(persisted_evidence_ids), separators=(",", ":")),
            json.dumps(list(application_ids), separators=(",", ":")),
            json.dumps(list(transition_ids), separators=(",", ":")),
            json.dumps(elimination_payload, separators=(",", ":"), default=base.json_default),
            json.dumps(elimination_context, separators=(",", ":"), default=base.json_default),
            bucket_elimination.ELIMINATION_MODEL_VERSION,
            signal_id,
        ),
    )


def process_weather(conn: psycopg.Connection[Any], weather: dict[str, Any]) -> int:
    station = weather["station_code"]
    meta = STATIONS.get(station)
    if not meta:
        return 0
    strategy = base.dbn_strategy(conn)
    if not strategy:
        return 0

    timezone_name = meta.get("timezone")
    if not timezone_name:
        base.audit_error(conn, "MISSING_STATION_TIMEZONE", {"weather_id": weather["id"]}, station)
        return 0

    proof = hard_state_proof.proof_for_weather(
        conn,
        session_id=base.SESSION_ID,
        weather=weather,
        timezone_name=timezone_name,
    )
    if proof is None:
        return 0

    # Persist every accepted derivation first, including lower/equal evidence.
    # This is what lets later six-hour disclosure remain visible as corroboration
    # without rewriting the first-known transition.
    persisted_evidence_ids = _persist_canonical_evidence(conn, proof, station)
    timeline = _timeline_from_proof(proof, station)

    application_ids: tuple[str, ...] = ()
    transition_ids: tuple[str, ...] = ()
    if timeline is not None and _immutable_history_complete(proof):
        application_ids, transition_ids = hard_state_journal.persist_timeline(
            conn,
            session_id=base.SESSION_ID,
            timeline=timeline,
        )

    if timeline is not None and timeline.current_state is not None:
        state = timeline.current_state
    else:
        # Historical compatibility only. New Step-4B captures always have a
        # canonical evidence history and therefore use the accumulator path.
        state = proof.to_hard_state(station)

    if not _current_weather_created_transition(proof, station, state, int(weather["id"])):
        return 0

    transition_record = _transition_record(proof, station, state)
    confirmed_high = Decimal(state.proven_daily_high_min_f)
    proof_payload = proof.as_dict()
    state_payload = state.to_dict()
    trigger_epoch_ms = int(state.first_known_at.timestamp() * 1000)
    trigger_grade = transition_record.grade if transition_record is not None else proof.trigger_grade
    trigger_raw_group = transition_record.raw_group if transition_record is not None else proof.trigger_raw_group

    series = meta["series"]
    series_rules = base.series_rules_before(conn, series, state.first_known_at)
    # Do not pre-decide event date here. Every candidate snapshot goes through
    # the pure elimination engine, which independently enforces station/date.
    event_rules = base.event_rule_candidates(conn, series, state.first_known_at)

    candidates: list[base.Candidate] = []
    for event in event_rules:
        event_for_elimination = dict(event)
        # Kalshi event snapshots do not carry the settlement-station ICAO code;
        # the series/station adapter that selected this event supplies it here.
        event_for_elimination["station_code"] = station
        result = bucket_elimination.evaluate_event(event_for_elimination, state)
        if not result.accepted:
            # Other open daily events are expected to fail the date guard; only
            # malformed metadata for the target date is an audit error.
            if result.fail_closed_reason != "event_climate_date_mismatch":
                base.audit_error(conn, "BUCKET_ELIMINATION_FAIL_CLOSED", {
                    "weather_id": weather["id"],
                    "event_ticker": event.get("event_ticker"),
                    "hard_state_id": state.state_id,
                    "reason": result.fail_closed_reason,
                    "elimination_model_version": bucket_elimination.ELIMINATION_MODEL_VERSION,
                }, station)
            continue

        markets_by_ticker = {
            str(market.get("ticker") or ""): market
            for market in event["markets"]
            if isinstance(market, dict) and market.get("ticker")
        }
        context_payload = {
            "event_ticker": result.event_ticker,
            "station_code": result.station_code,
            "climate_date": result.climate_date,
            "hard_state_id": result.hard_state_id,
            "transition_evidence_id": result.transition_evidence_id,
            "event_rules_hash": result.event_rules_hash,
            "elimination_model_version": result.elimination_model_version,
            "dead_market_tickers": list(result.dead_market_tickers),
        }

        for elimination in result.eliminated:
            market = markets_by_ticker.get(elimination.market_ticker)
            if market is None:
                base.audit_error(conn, "ELIMINATED_MARKET_NOT_IN_SNAPSHOT", {
                    "event_ticker": result.event_ticker,
                    "market_ticker": elimination.market_ticker,
                    "elimination_id": elimination.elimination_id,
                }, station)
                continue
            upper = base.market_upper_bound(market)
            if upper is None:
                # Should be unreachable: the elimination engine cannot eliminate
                # an unbounded upper tail. Preserve fail-closed behavior anyway.
                base.audit_error(conn, "ELIMINATION_WITHOUT_FINITE_CAP", {
                    "event_ticker": result.event_ticker,
                    "market_ticker": elimination.market_ticker,
                    "elimination_id": elimination.elimination_id,
                }, station)
                continue

            signal_id, auditor_status = base.insert_signal(
                conn,
                weather=weather,
                event_ticker=event["event_ticker"],
                market=market,
                upper_bound=upper,
                confirmed_high=confirmed_high,
                event_rules=event,
                series_rules=series_rules,
                proven=True,
            )
            _attach_proof_to_signal(
                conn,
                signal_id,
                proof,
                state,
                persisted_evidence_ids,
                timeline,
                application_ids,
                transition_ids,
                elimination,
                context_payload,
            )

            if auditor_status != "approved" or not strategy["paper_trade_enabled"]:
                continue
            if not series_rules or not series_rules.get("fee_type") or series_rules.get("fee_multiplier") is None:
                continue
            candidates.append(base.Candidate(
                signal_id=signal_id,
                station=station,
                region=meta.get("region", station),
                event_ticker=event["event_ticker"],
                market_ticker=elimination.market_ticker,
                trigger_at=state.first_known_at,
                trigger_epoch_ms=trigger_epoch_ms,
                confirmed_high_f=confirmed_high,
                upper_bound_f=upper,
                fee_type=str(series_rules["fee_type"]),
                fee_multiplier=base.d(series_rules["fee_multiplier"]),
                evidence={
                    "weather_event_id": weather["id"],
                    "event_rules_hash": event["rules_hash"],
                    "series_rules_hash": series_rules["rules_hash"],
                    "hard_state_proof": proof_payload,
                    "hard_climate_state": state_payload,
                    "hard_state_timeline": timeline.to_dict() if timeline is not None else None,
                    "proof_version": hard_state_proof.PROOF_VERSION,
                    "hard_state_model_version": state.state_model_version,
                    "proven_daily_high_min_f": confirmed_high,
                    "upper_bound_f": upper,
                    "region": meta.get("region", station),
                    "climate_trade_date": state.climate_date.isoformat(),
                    "trigger_evidence_grade": trigger_grade,
                    "trigger_raw_group": trigger_raw_group,
                    "transition_evidence_id": state.transition_evidence_id,
                    "source_weather_ids_at_bound": list(proof.source_weather_ids_at_bound),
                    "raw_source_ids_at_bound": list(proof.raw_source_ids_at_bound),
                    "immutable_provenance_complete": proof.immutable_provenance_complete,
                    "evidence_derivation_ids": list(persisted_evidence_ids),
                    "hard_state_application_ids": list(application_ids),
                    "hard_state_transition_ids": list(transition_ids),
                    "bucket_elimination": elimination.to_dict(),
                    "elimination_context": context_payload,
                    "elimination_model_version": bucket_elimination.ELIMINATION_MODEL_VERSION,
                    "station_timezone": timezone_name,
                },
            ))

    if candidates:
        global_cfg = base.load_global(conn)
        modes = base.load_modes(conn)
        base.execute_candidates(conn, candidates, modes, global_cfg)
    return len(candidates)
