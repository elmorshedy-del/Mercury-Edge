from __future__ import annotations

"""Rigorous live-paper strategy coordinator.

Benchmark sleeves spend cash only on signals whose strategy configuration says
``confidence_gate=approved_only`` *and* whose bundle auditor status is approved.
Everything else can still be simulated in the independent shadow lab.

Deterministic hard-state strategies (DSN/SBK/HSR) are constructed only from the
raw-ASOS proof layer. Probabilistic/precision research remains on the legacy
weather context until its own dedicated refactor steps, preventing the two
classes from being silently mixed.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg

import hard_state_proof
import risk_controls
import shadow_lab
import strategy_engines as se
import strategy_runtime as legacy
from market_calendar import confirmed_same_day_high, event_matches_observation, local_date

_ORIGINAL_PORTFOLIO_BUDGET = se.portfolio_budget
_HARD_CORE = {"DSN", "SBK", "HSR"}


def _risk_adjusted_portfolio_budget(
    conn: psycopg.Connection[Any],
    mode: dict[str, Any],
    bundle: se.Bundle,
    global_cfg: dict[str, Any],
    strategy_cfg: dict[str, Any],
) -> Decimal:
    raw = _ORIGINAL_PORTFOLIO_BUDGET(conn, mode, bundle, global_cfg, strategy_cfg)
    return risk_controls.apply_budget_multiplier(
        conn,
        int(mode["portfolio_id"]),
        raw,
        global_cfg,
    )


# se.execute_bundle resolves this global at call time, so the wrapper covers any
# extra strategy that is later promoted into the benchmark cohort.
se.portfolio_budget = _risk_adjusted_portfolio_budget


def _benchmark_eligible(bundle: se.Bundle, cfg: dict[str, Any]) -> bool:
    config = cfg.get("config", {}) if isinstance(cfg.get("config"), dict) else {}
    gate = str(config.get("confidence_gate", "shadow_only"))
    return (
        bool(cfg.get("paper_trade_enabled"))
        and gate == "approved_only"
        and bundle.auditor_status == "approved"
    )


def _hard_transition_weather(
    conn: psycopg.Connection[Any],
    session_id: str,
    weather: dict[str, Any],
    timezone_name: str,
) -> tuple[hard_state_proof.HardStateProof | None, dict[str, Any] | None]:
    """Create a strategy weather view only when raw evidence raises the daily lower bound."""
    proof = hard_state_proof.proof_for_weather(
        conn,
        session_id=session_id,
        weather=weather,
        timezone_name=timezone_name,
    )
    if proof is None or not proof.is_new_transition(weather["id"]):
        return None, None
    guarded = dict(weather)
    guarded["max_temperature_f"] = Decimal(proof.proven_daily_high_min_f)
    guarded["first_seen_at"] = proof.trigger_at
    guarded["received_epoch_ms"] = proof.trigger_epoch_ms
    return proof, guarded


def _attach_hard_proof(bundle: se.Bundle, proof: hard_state_proof.HardStateProof, timezone_name: str) -> None:
    bundle.evidence["hard_state_proof"] = proof.as_dict()
    bundle.evidence["proof_version"] = "raw-asos-lattice-v1"
    bundle.evidence["proven_daily_high_min_f"] = proof.proven_daily_high_min_f
    bundle.evidence["climate_trade_date"] = proof.climate_trade_date.isoformat()
    bundle.evidence["trigger_evidence_grade"] = proof.trigger_grade
    bundle.evidence["trigger_raw_group"] = proof.trigger_raw_group
    bundle.evidence["source_weather_ids_at_bound"] = list(proof.source_weather_ids_at_bound)
    bundle.evidence["station_timezone"] = timezone_name


def _validate_rmo_prior(
    conn: psycopg.Connection[Any],
    bundle: se.Bundle | None,
    weather: dict[str, Any],
    timezone_name: str,
    cfg: dict[str, Any],
) -> se.Bundle | None:
    if bundle is None:
        return None
    prior_id = bundle.evidence.get("prior_weather_id")
    if prior_id is None:
        return None
    row = conn.execute(
        "SELECT observed_at,first_seen_at FROM live_weather_journal WHERE id=%s",
        (prior_id,),
    ).fetchone()
    if not row:
        return None
    prior_observed, prior_seen = row
    current_observed = weather.get("observed_at")
    current_seen = weather.get("first_seen_at")
    if not isinstance(current_observed, datetime) or not isinstance(current_seen, datetime):
        return None
    max_age = max(1, int(cfg.get("prior_max_age_seconds", 180)))
    observation_gap = (current_observed - prior_observed).total_seconds()
    receipt_gap = (current_seen - prior_seen).total_seconds()
    if observation_gap <= 0 or observation_gap > max_age or receipt_gap <= 0:
        return None
    if local_date(prior_observed, timezone_name) != local_date(current_observed, timezone_name):
        return None
    bundle.evidence["prior_observation_gap_seconds"] = observation_gap
    bundle.evidence["prior_receipt_gap_seconds"] = receipt_gap
    return bundle


def _expand_hsr_routes(bundle: se.Bundle, strategy_cfg: dict[str, Any]) -> None:
    if bundle.strategy_code != "HSR":
        return
    config = cfg_obj = strategy_cfg.get("config", {}) if isinstance(strategy_cfg.get("config"), dict) else {}
    dead = [str(x) for x in bundle.evidence.get("dead_markets", []) if x]
    if not dead:
        return
    max_price = se.d(cfg_obj.get("max_entry_price", 0.97))
    routes: dict[str, list[se.Leg]] = {
        f"dbn:{ticker}": [se.Leg(ticker, "no", bundle.trigger_epoch_ms, max_price, "route_dbn")]
        for ticker in dead
    }
    if len(dead) >= 2:
        routes["dsn"] = [se.Leg(ticker, "no", bundle.trigger_epoch_ms, max_price, "route_dsn") for ticker in dead]
    if bundle.route_options and bundle.route_options.get("sbk"):
        routes["sbk"] = bundle.route_options["sbk"]
    bundle.route_options = routes
    bundle.evidence["routes"] = list(routes)


def execute_extra_strategies(
    conn: psycopg.Connection[Any],
    session_id: str,
    weather: dict[str, Any],
    modes: list[dict[str, Any]],
    global_cfg: dict[str, Any],
) -> int:
    meta = se.STATIONS.get(weather["station_code"])
    if not meta:
        return 0
    timezone_name = meta.get("timezone")
    if not timezone_name:
        return 0

    # Deterministic path: only a newly raised raw-ASOS lower bound can create
    # DSN/SBK/HSR bundles.
    proof, hard_weather = _hard_transition_weather(conn, session_id, weather, timezone_name)

    # Research path: preserve the prior behavior until the dedicated predictive
    # separation step. This value never feeds DSN/SBK/HSR after this refactor.
    same_day_high = confirmed_same_day_high(
        conn,
        session_id=session_id,
        station_code=weather["station_code"],
        observed_at=weather["observed_at"],
        first_seen_at=weather["first_seen_at"],
        timezone_name=timezone_name,
    )
    research_weather = dict(weather)
    if same_day_high is not None:
        research_weather["max_temperature_f"] = same_day_high.value_f

    if proof is None and same_day_high is None:
        return 0

    configs = se.strategy_configs(conn)
    series = meta["series"]
    rules = se.series_rules_before(conn, series, weather["first_seen_at"])
    if not rules or rules.get("fee_type") not in ("quadratic", "quadratic_with_maker_fees") or rules.get("fee_multiplier") is None:
        return 0

    fee_multiplier = se.d(rules["fee_multiplier"])
    legacy_proven = se.compatibility_proven(conn, weather)
    events = [
        event
        for event in se.event_rules_before(conn, series, weather["first_seen_at"])
        if event_matches_observation(event["event_ticker"], weather["observed_at"], timezone_name)
    ]
    created = 0

    for event in events:
        bundles: list[se.Bundle] = []

        if proof is not None and hard_weather is not None:
            hard_configs = {code: configs[code] for code in _HARD_CORE if code in configs}
            hard_bundles = se.construct_hard_bundles(
                hard_weather,
                meta,
                event,
                True,
                hard_configs,
            )
            for bundle in hard_bundles:
                _attach_hard_proof(bundle, proof, timezone_name)
            bundles.extend(hard_bundles)

        # Everything below remains research semantics for this step. It is
        # deliberately not allowed to feed the deterministic hard-core bundles.
        if same_day_high is not None and configs.get("WTY", {}).get("enabled"):
            item = se.construct_wty(conn, session_id, research_weather, meta, event, configs["WTY"]["config"])
            if item:
                bundles.append(item)

        rmo = None
        if same_day_high is not None and configs.get("RMO", {}).get("enabled"):
            rmo_cfg = configs["RMO"]["config"]
            rmo = _validate_rmo_prior(
                conn,
                se.construct_rmo(conn, session_id, research_weather, meta, event, rmo_cfg),
                research_weather,
                timezone_name,
                rmo_cfg,
            )
            if rmo:
                bundles.append(rmo)

        prvs = (
            se.construct_prv(research_weather, meta, event, configs.get("PRV", {}).get("config", {}))
            if same_day_high is not None and configs.get("PRV", {}).get("enabled") else []
        )
        bundles.extend(prvs)

        # Sequential research strategies are still generated prospectively, but
        # their benchmark eligibility is decided only after signal creation.
        if rmo and configs.get("LVP", {}).get("enabled"):
            leg = rmo.legs[0]
            bundles.append(se._bundle(
                "LVP", "sequential_hypothesis", research_weather, meta, event,
                [se.Leg(leg.market_ticker, leg.side, leg.trigger_epoch_ms, leg.max_price, "lvp_phase1_rmo")],
                {"phase": 1, "source_strategy": "RMO", "rmo_evidence": rmo.evidence},
                True,
            ))
        if rmo and configs.get("HMF", {}).get("enabled"):
            leg = rmo.legs[0]
            bundles.append(se._bundle(
                "HMF", "sequential_hypothesis", research_weather, meta, event,
                [se.Leg(leg.market_ticker, leg.side, leg.trigger_epoch_ms, leg.max_price, "hmf_phase1_rmo")],
                {"phase": 1, "source_strategy": "RMO", "rmo_evidence": rmo.evidence},
                True,
            ))

        if prvs and configs.get("LVP", {}).get("enabled"):
            prior = se.linked_signal(
                conn, session_id, weather["station_code"], event["event_ticker"], "LVP",
                weather["first_seen_at"], int(configs["LVP"]["config"].get("pair_window_minutes", 180)),
            )
            if prior:
                for prv in prvs:
                    leg = prv.legs[0]
                    bundles.append(se._bundle(
                        "LVP", "sequential_hypothesis", research_weather, meta, event,
                        [se.Leg(leg.market_ticker, leg.side, leg.trigger_epoch_ms, leg.max_price, "lvp_phase2_prv")],
                        {"phase": 2, "prior_lvp_signal_id": prior["id"], "prv_evidence": prv.evidence},
                        True,
                    ))

        if same_day_high is not None and legacy_proven and configs.get("HMF", {}).get("enabled"):
            prior = se.linked_signal(
                conn, session_id, weather["station_code"], event["event_ticker"], "HMF",
                weather["first_seen_at"], int(configs["HMF"]["config"].get("pair_window_minutes", 240)),
            )
            if prior:
                high = same_day_high.value_f
                dead = [m for m in event["markets"] if se.upper_bound(m) is not None and high > se.upper_bound(m)]
                if dead:
                    market = dead[-1]
                    ticker = str(market.get("ticker") or "")
                    if ticker:
                        bundles.append(se._bundle(
                            "HMF", "hard_state_sequence", research_weather, meta, event,
                            [se.Leg(
                                ticker, "no", weather["received_epoch_ms"],
                                se.d(configs["HMF"]["config"].get("max_no_price", 0.97)),
                                "hmf_phase2_hidden_max",
                            )],
                            {
                                "phase": 2,
                                "prior_hmf_signal_id": prior["id"],
                                "confirmed_high_f": high,
                                "compatibility_rule": weather.get("compatibility_rule"),
                                "research_legacy_weather_semantics": True,
                            },
                            True,
                        ))

        bundles.sort(
            key=lambda b: int(configs.get(b.strategy_code, {}).get("config", {}).get("capital_priority", 0)),
            reverse=True,
        )

        for bundle in bundles:
            cfg = configs.get(bundle.strategy_code)
            if not cfg or not cfg.get("enabled"):
                continue

            if bundle.strategy_code in _HARD_CORE:
                # A hard-core bundle can exist only through the proof branch.
                if proof is None or "hard_state_proof" not in bundle.evidence:
                    continue
            elif same_day_high is not None:
                bundle.evidence["local_trade_date"] = same_day_high.local_trade_date.isoformat()
                bundle.evidence["same_day_high_weather_ids"] = list(same_day_high.evidence_weather_ids)
                bundle.evidence["awc_six_hour_max_weather_ids"] = list(same_day_high.used_awc_six_hour_max_ids)
                bundle.evidence["station_timezone"] = timezone_name
                bundle.evidence["research_legacy_weather_semantics"] = True

            _expand_hsr_routes(bundle, cfg)

            signal_id = se.insert_signal(conn, session_id, bundle)
            if bool(cfg.get("shadow_enabled", True)):
                shadow_lab.record_bundle_trials(
                    conn,
                    session_id=session_id,
                    bundle=bundle,
                    signal_id=signal_id,
                    global_cfg=global_cfg,
                    strategy_cfg=cfg,
                    fee_multiplier=fee_multiplier,
                )

            if _benchmark_eligible(bundle, cfg):
                legacy.execute_with_strategy_guard(
                    conn, session_id, bundle, signal_id, modes, global_cfg, cfg, fee_multiplier,
                )
            created += 1

    return created
