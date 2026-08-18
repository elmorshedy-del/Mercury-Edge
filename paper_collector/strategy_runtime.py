from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg

import strategy_engines as se


def _skip(conn: psycopg.Connection[Any], mode: dict[str, Any], signal_id: int, reason: str, details: dict[str, Any], score: Decimal | None = None) -> None:
    se._record_decision(
        conn,
        int(mode["portfolio_id"]),
        signal_id,
        "skip",
        Decimal(0),
        score,
        reason,
        details,
    )


def execute_with_strategy_guard(
    conn: psycopg.Connection[Any],
    session_id: str,
    bundle: se.Bundle,
    signal_id: int,
    modes: list[dict[str, Any]],
    global_cfg: dict[str, Any],
    strategy_cfg: dict[str, Any],
    fee_multiplier: Decimal,
) -> None:
    cfg = strategy_cfg.get("config", {})

    if bundle.strategy_code == "WTY":
        min_edge = se.d(cfg.get("min_model_edge", 0.03))
        leg = bundle.legs[0]
        model_probability = leg.model_probability
        if model_probability is None:
            for mode in modes:
                _skip(conn, mode, signal_id, "WTY_MODEL_PROBABILITY_MISSING", {})
            return
        for mode in modes:
            latency = max(0, int(mode["config"].get("execution_latency_ms", 100)))
            book = se.reconstruct_full_book(conn, session_id, leg.market_ticker, leg.trigger_epoch_ms + latency)
            levels = se.asks(book, "yes", leg.max_price) if book else []
            if not levels:
                _skip(conn, mode, signal_id, "WTY_NO_EXECUTABLE_YES_ASK", {"latency_ms": latency})
                continue
            current_ask = levels[0][0]
            edge = model_probability - current_ask
            if edge < min_edge:
                _skip(conn, mode, signal_id, "WTY_MODEL_EDGE_TOO_SMALL", {
                    "model_probability": model_probability,
                    "entry_ask": current_ask,
                    "model_edge": edge,
                    "required_edge": min_edge,
                }, edge)
                continue
            se.execute_bundle(conn, session_id, bundle, signal_id, [mode], global_cfg, strategy_cfg, fee_multiplier)
        return

    if bundle.strategy_code == "SBK":
        min_edge = se.d(cfg.get("min_net_edge", 0.01))
        inter_leg = max(0, int(cfg.get("multi_leg_inter_order_ms", 5)))
        for mode in modes:
            latency = max(0, int(mode["config"].get("execution_latency_ms", 100)))
            one_unit_total = Decimal(0)
            executable = True
            details: list[dict[str, str]] = []
            for index, leg in enumerate(bundle.legs):
                arrival = leg.trigger_epoch_ms + latency + index * inter_leg
                book = se.reconstruct_full_book(conn, session_id, leg.market_ticker, arrival)
                levels = se.asks(book, "yes", leg.max_price) if book else []
                if not levels:
                    executable = False
                    break
                price = levels[0][0]
                cost, fee, _ = se.total_cost([(price, Decimal(1))], fee_multiplier)
                one_unit_total += cost
                details.append({
                    "market": leg.market_ticker,
                    "ask": format(price, "f"),
                    "cost_after_fee": format(cost, "f"),
                    "fee": format(fee, "f"),
                })
            if not executable:
                _skip(conn, mode, signal_id, "SBK_INCOMPLETE_EXECUTABLE_BASKET", {"latency_ms": latency})
                continue
            edge = Decimal(1) - one_unit_total
            if edge < min_edge:
                _skip(conn, mode, signal_id, "SBK_NET_EDGE_TOO_SMALL", {
                    "one_unit_basket_cost_after_fees": one_unit_total,
                    "net_edge": edge,
                    "required_edge": min_edge,
                    "legs": details,
                }, edge)
                continue
            se.execute_bundle(conn, session_id, bundle, signal_id, [mode], global_cfg, strategy_cfg, fee_multiplier)
        return

    se.execute_bundle(conn, session_id, bundle, signal_id, modes, global_cfg, strategy_cfg, fee_multiplier)


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
    configs = se.strategy_configs(conn)
    series = meta["series"]
    rules = se.series_rules_before(conn, series, weather["first_seen_at"])
    if not rules or rules.get("fee_type") not in ("quadratic", "quadratic_with_maker_fees") or rules.get("fee_multiplier") is None:
        return 0

    fee_multiplier = se.d(rules["fee_multiplier"])
    proven = se.compatibility_proven(conn, weather)
    events = se.event_rules_before(conn, series, weather["first_seen_at"])
    created = 0

    for event in events:
        # DBN remains in the original, independently tested engine and is called
        # immediately before this function by unified_engine.py.
        bundles = se.construct_hard_bundles(
            weather,
            meta,
            event,
            proven,
            {k: v for k, v in configs.items() if k != "DBN"},
        )

        if configs.get("WTY", {}).get("enabled"):
            item = se.construct_wty(conn, session_id, weather, meta, event, configs["WTY"]["config"])
            if item:
                bundles.append(item)

        rmo = None
        if configs.get("RMO", {}).get("enabled"):
            rmo = se.construct_rmo(conn, session_id, weather, meta, event, configs["RMO"]["config"])
            if rmo:
                bundles.append(rmo)

        prvs = se.construct_prv(weather, meta, event, configs.get("PRV", {}).get("config", {})) if configs.get("PRV", {}).get("enabled") else []
        bundles.extend(prvs)

        # Sequential strategies enter phase 1 prospectively. No retroactive fill
        # is fabricated when a later reversal appears.
        if rmo and configs.get("LVP", {}).get("enabled"):
            leg = rmo.legs[0]
            bundles.append(se._bundle(
                "LVP", "sequential_hypothesis", weather, meta, event,
                [se.Leg(leg.market_ticker, leg.side, leg.trigger_epoch_ms, leg.max_price, "lvp_phase1_rmo")],
                {"phase": 1, "source_strategy": "RMO", "rmo_evidence": rmo.evidence},
                True,
            ))
        if rmo and configs.get("HMF", {}).get("enabled"):
            leg = rmo.legs[0]
            bundles.append(se._bundle(
                "HMF", "sequential_hypothesis", weather, meta, event,
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
                        "LVP", "sequential_hypothesis", weather, meta, event,
                        [se.Leg(leg.market_ticker, leg.side, leg.trigger_epoch_ms, leg.max_price, "lvp_phase2_prv")],
                        {"phase": 2, "prior_lvp_signal_id": prior["id"], "prv_evidence": prv.evidence},
                        True,
                    ))

        if proven and configs.get("HMF", {}).get("enabled"):
            prior = se.linked_signal(
                conn, session_id, weather["station_code"], event["event_ticker"], "HMF",
                weather["first_seen_at"], int(configs["HMF"]["config"].get("pair_window_minutes", 240)),
            )
            if prior:
                high = weather.get("max_temperature_f") or weather.get("temperature_f")
                if high is not None:
                    dead = [m for m in event["markets"] if se.upper_bound(m) is not None and se.d(high) > se.upper_bound(m)]
                    if dead:
                        market = dead[-1]
                        ticker = str(market.get("ticker") or "")
                        if ticker:
                            bundles.append(se._bundle(
                                "HMF", "hard_state_sequence", weather, meta, event,
                                [se.Leg(
                                    ticker, "no", weather["received_epoch_ms"],
                                    se.d(configs["HMF"]["config"].get("max_no_price", 0.97)),
                                    "hmf_phase2_hidden_max",
                                )],
                                {
                                    "phase": 2,
                                    "prior_hmf_signal_id": prior["id"],
                                    "confirmed_high_f": se.d(high),
                                    "compatibility_rule": weather.get("compatibility_rule"),
                                },
                                True,
                            ))

        # Stable deterministic priority: hard-state/router first, then research.
        bundles.sort(
            key=lambda b: int(configs.get(b.strategy_code, {}).get("config", {}).get("capital_priority", 0)),
            reverse=True,
        )

        for bundle in bundles:
            cfg = configs.get(bundle.strategy_code)
            if not cfg or not cfg.get("enabled"):
                continue
            signal_id = se.insert_signal(conn, session_id, bundle)
            execute_with_strategy_guard(
                conn, session_id, bundle, signal_id, modes, global_cfg, cfg, fee_multiplier,
            )
            created += 1

    return created
