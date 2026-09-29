from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from typing import Any

import psycopg

import paper_engine_hardened as dbn
from strategy_runtime_hardened import execute_extra_strategies

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.environ["PAPER_SESSION_ID"]
POLL_MS = max(50, int(os.getenv("PAPER_ENGINE_POLL_MS", "250")))
DEFAULT_PROCESS_DELAY_MS = max(1000, int(os.getenv("PAPER_ENGINE_PROCESS_DELAY_MS", "6000")))
STOP = False


def stop(*_: object) -> None:
    global STOP
    STOP = True


def weather_is_fresh_for_live_trade(weather: dict[str, Any], global_cfg: dict[str, Any]) -> tuple[bool, str | None]:
    """Keep catch-up/backfilled weather for research but never trade it as live."""
    observed = weather.get("observed_at")
    first_seen = weather.get("first_seen_at")
    if not isinstance(observed, datetime) or not isinstance(first_seen, datetime):
        return False, "MISSING_WEATHER_TIMESTAMPS"
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)

    source = str(weather.get("source") or "")
    if source == "IEM_MADIS_OMO":
        source_max = float(global_cfg.get("omo_live_max_source_age_seconds", 90))
    elif source == "NOAA_AWC":
        source_max = float(global_cfg.get("awc_live_max_source_age_seconds", 300))
    else:
        source_max = float(global_cfg.get("weather_live_max_source_age_seconds", 180))
    processing_max = float(global_cfg.get("paper_live_max_processing_age_seconds", 30))

    source_age = (first_seen - observed).total_seconds()
    processing_age = (datetime.now(timezone.utc) - first_seen.astimezone(timezone.utc)).total_seconds()
    if source_age < -5:
        return False, "OBSERVATION_TIMESTAMP_IN_FUTURE"
    if source_age > source_max:
        return False, "CATCHUP_WEATHER_TOO_OLD"
    if processing_age < -5:
        return False, "FIRST_SEEN_TIMESTAMP_IN_FUTURE"
    if processing_age > processing_max:
        return False, "ENGINE_BACKLOG_TOO_OLD"
    return True, None


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({
        "event": "unified_paper_engine_start",
        "session_id": SESSION_ID,
        "engines": ["DBN", "DSN", "SBK", "HSR", "WTY", "RMO", "PRV", "LVP", "HMF"],
        "benchmark_gate": "approved_only",
        "shadow_lab": True,
        "real_money": False,
    }))

    with psycopg.connect(DATABASE_URL) as conn:
        dbn.ensure_session_and_portfolios(conn)
        conn.execute(
            """
            INSERT INTO paper_engine_state(session_id,component,last_weather_id)
            VALUES (%s,'unified_engine',0)
            ON CONFLICT (session_id,component) DO NOTHING
            """,
            (SESSION_ID,),
        )
        conn.commit()

        while not STOP:
            started = time.monotonic()
            global_cfg = dbn.load_global(conn)
            if not bool(global_cfg.get("paper_enabled", True)):
                time.sleep(max(0.05, POLL_MS / 1000))
                continue

            delay_ms = max(1000, int(global_cfg.get("paper_engine_process_delay_ms", DEFAULT_PROCESS_DELAY_MS)))
            state = conn.execute(
                "SELECT last_weather_id FROM paper_engine_state WHERE session_id=%s AND component='unified_engine'",
                (SESSION_ID,),
            ).fetchone()
            last_id = int(state[0]) if state else 0
            cutoff_ms = time.time_ns() // 1_000_000 - delay_ms
            rows = conn.execute(
                """
                SELECT id,station_code,source,report_type,observed_at,first_seen_at,
                       received_epoch_ms,received_epoch_ns,temperature_f,max_temperature_f,
                       compatibility_status,compatibility_rule
                  FROM live_weather_journal
                 WHERE session_id=%s AND id>%s AND received_epoch_ms<=%s
                 ORDER BY id ASC LIMIT 100
                """,
                (SESSION_ID, last_id, cutoff_ms),
            ).fetchall()

            if not rows:
                elapsed = time.monotonic() - started
                time.sleep(max(0.05, POLL_MS / 1000 - elapsed))
                continue

            stale_counts: dict[str, int] = {}
            for raw in rows:
                weather = dbn.weather_row(raw)
                try:
                    with conn.transaction():
                        global_cfg = dbn.load_global(conn)
                        fresh, stale_reason = weather_is_fresh_for_live_trade(weather, global_cfg)
                        if fresh:
                            # Benchmark DBN gets first claim only when approved;
                            # research strategies are evaluated independently in
                            # the counterfactual lab unless explicitly promoted.
                            dbn_count = dbn.process_weather(conn, weather)
                            modes = dbn.load_modes(conn)
                            extra_count = execute_extra_strategies(
                                conn,
                                SESSION_ID,
                                weather,
                                modes,
                                global_cfg,
                            )
                        else:
                            dbn_count = 0
                            extra_count = 0
                            reason = stale_reason or "STALE_WEATHER"
                            stale_counts[reason] = stale_counts.get(reason, 0) + 1

                        conn.execute(
                            """
                            UPDATE paper_engine_state
                               SET last_weather_id=%s,updated_at=now()
                             WHERE session_id=%s AND component='unified_engine'
                            """,
                            (weather["id"], SESSION_ID),
                        )
                    if dbn_count or extra_count:
                        print(json.dumps({
                            "event": "unified_weather_processed",
                            "weather_id": weather["id"],
                            "station": weather["station_code"],
                            "dbn_candidates": dbn_count,
                            "other_strategy_signals": extra_count,
                        }))
                except Exception as exc:
                    conn.rollback()
                    with conn.transaction():
                        dbn.audit_error(conn, "UNIFIED_ENGINE_WEATHER_FAILED", {
                            "weather_id": weather["id"],
                            "error": repr(exc),
                        }, weather["station_code"])
                        conn.execute(
                            """
                            UPDATE paper_engine_state
                               SET last_weather_id=%s,updated_at=now()
                             WHERE session_id=%s AND component='unified_engine'
                            """,
                            (weather["id"], SESSION_ID),
                        )

            conn.commit()
            if stale_counts:
                print(json.dumps({
                    "event": "stale_weather_catchup_skipped",
                    "session_id": SESSION_ID,
                    "counts": stale_counts,
                }))

    print(json.dumps({"event": "unified_paper_engine_stop", "session_id": SESSION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
