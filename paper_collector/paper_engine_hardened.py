from __future__ import annotations

"""Fail-closed wrapper around the original DBN paper engine.

The original implementation is retained for replay compatibility. Live paper
execution imports this module instead so DBN spends benchmark cash only when a
raw-ASOS proof object establishes that the relevant bucket is impossible.
"""

import json
from decimal import Decimal
from typing import Any

import psycopg

import hard_state_proof
import paper_engine as base
import risk_controls
from market_calendar import event_matches_observation
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


def _attach_proof_to_signal(
    conn: psycopg.Connection[Any],
    signal_id: int,
    proof: hard_state_proof.HardStateProof,
) -> None:
    """Persist the exact raw-evidence proof alongside the DBN signal audit record."""
    payload = proof.as_dict()
    conn.execute(
        """
        UPDATE paper_signals
           SET evidence = evidence || jsonb_build_object(
                 'hard_state_proof', %s::jsonb,
                 'proof_version', 'raw-asos-lattice-v1',
                 'proven_daily_high_min_f', %s,
                 'climate_trade_date', %s,
                 'trigger_evidence_grade', %s,
                 'trigger_raw_group', %s
               )
         WHERE id=%s
        """,
        (
            json.dumps(payload, separators=(",", ":"), default=base.json_default),
            proof.proven_daily_high_min_f,
            proof.climate_trade_date.isoformat(),
            proof.trigger_grade,
            proof.trigger_raw_group,
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
    if proof is None or not proof.is_new_transition(weather["id"]):
        return 0

    # This is deliberately an integer lower bound from the raw-ASOS lattice.
    # Decoded temperature_f/max_temperature_f values in the collector row have
    # no authority over deterministic bucket elimination.
    confirmed_high = Decimal(proof.proven_daily_high_min_f)
    proof_payload = proof.as_dict()

    series = meta["series"]
    series_rules = base.series_rules_before(conn, series, proof.trigger_at)
    event_rules = [
        event
        for event in base.event_rule_candidates(conn, series, proof.trigger_at)
        if event_matches_observation(event["event_ticker"], weather["observed_at"], timezone_name)
    ]

    candidates: list[base.Candidate] = []
    for event in event_rules:
        for market in event["markets"]:
            upper = base.market_upper_bound(market)
            ticker = str(market.get("ticker") or "")
            if not ticker or upper is None or not proof.proves_above(upper):
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
                # Raw-ASOS proof replaces the old generic decoded-weather
                # compatibility gate for hard-state classification.
                proven=True,
            )
            _attach_proof_to_signal(conn, signal_id, proof)

            # Benchmark capital is strictly approved-only. Research/shadow
            # signals are handled independently by the experiment ledger.
            if auditor_status != "approved" or not strategy["paper_trade_enabled"]:
                continue
            if not series_rules or not series_rules.get("fee_type") or series_rules.get("fee_multiplier") is None:
                continue
            candidates.append(base.Candidate(
                signal_id=signal_id,
                station=station,
                region=meta.get("region", station),
                event_ticker=event["event_ticker"],
                market_ticker=ticker,
                trigger_at=proof.trigger_at,
                trigger_epoch_ms=proof.trigger_epoch_ms,
                confirmed_high_f=confirmed_high,
                upper_bound_f=upper,
                fee_type=str(series_rules["fee_type"]),
                fee_multiplier=base.d(series_rules["fee_multiplier"]),
                evidence={
                    "weather_event_id": weather["id"],
                    "event_rules_hash": event["rules_hash"],
                    "series_rules_hash": series_rules["rules_hash"],
                    "hard_state_proof": proof_payload,
                    "proof_version": "raw-asos-lattice-v1",
                    "proven_daily_high_min_f": confirmed_high,
                    "upper_bound_f": upper,
                    "region": meta.get("region", station),
                    "climate_trade_date": proof.climate_trade_date.isoformat(),
                    "trigger_evidence_grade": proof.trigger_grade,
                    "trigger_raw_group": proof.trigger_raw_group,
                    "source_weather_ids_at_bound": list(proof.source_weather_ids_at_bound),
                    "station_timezone": timezone_name,
                },
            ))

    if candidates:
        global_cfg = base.load_global(conn)
        modes = base.load_modes(conn)
        base.execute_candidates(conn, candidates, modes, global_cfg)
    return len(candidates)
