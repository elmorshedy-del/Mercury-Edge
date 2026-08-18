from __future__ import annotations

"""Fail-closed wrapper around the original DBN paper engine.

The original implementation is retained for replay compatibility.  Live paper
execution imports this module instead so the benchmark path gets stricter
calendar, same-day-high, and drawdown semantics without rewriting the proven
fill/fee code.
"""

from typing import Any

import psycopg

import paper_engine as base
import risk_controls
from market_calendar import confirmed_same_day_high, event_matches_observation
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

    high = confirmed_same_day_high(
        conn,
        session_id=base.SESSION_ID,
        station_code=station,
        observed_at=weather["observed_at"],
        first_seen_at=weather["first_seen_at"],
        timezone_name=timezone_name,
    )
    if high is None:
        return 0
    confirmed_high = high.value_f

    series = meta["series"]
    proven = base.compatibility_is_proven(conn, weather)
    series_rules = base.series_rules_before(conn, series, weather["first_seen_at"])
    event_rules = [
        event
        for event in base.event_rule_candidates(conn, series, weather["first_seen_at"])
        if event_matches_observation(event["event_ticker"], weather["observed_at"], timezone_name)
    ]

    candidates: list[base.Candidate] = []
    for event in event_rules:
        for market in event["markets"]:
            upper = base.market_upper_bound(market)
            ticker = str(market.get("ticker") or "")
            if not ticker or upper is None or confirmed_high <= upper:
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
                proven=proven,
            )
            # Benchmark capital is strictly approved-only.  Research/shadow
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
                trigger_at=weather["first_seen_at"],
                trigger_epoch_ms=weather["received_epoch_ms"],
                confirmed_high_f=confirmed_high,
                upper_bound_f=upper,
                fee_type=str(series_rules["fee_type"]),
                fee_multiplier=base.d(series_rules["fee_multiplier"]),
                evidence={
                    "weather_event_id": weather["id"],
                    "event_rules_hash": event["rules_hash"],
                    "series_rules_hash": series_rules["rules_hash"],
                    "compatibility_rule": weather["compatibility_rule"],
                    "confirmed_high_f": confirmed_high,
                    "upper_bound_f": upper,
                    "region": meta.get("region", station),
                    "local_trade_date": high.local_trade_date.isoformat(),
                    "same_day_high_weather_ids": list(high.evidence_weather_ids),
                    "awc_six_hour_max_weather_ids": list(high.used_awc_six_hour_max_ids),
                    "station_timezone": timezone_name,
                },
            ))

    if candidates:
        global_cfg = base.load_global(conn)
        modes = base.load_modes(conn)
        base.execute_candidates(conn, candidates, modes, global_cfg)
    return len(candidates)
