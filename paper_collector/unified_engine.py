from __future__ import annotations

import json
import os
import signal
import time
from typing import Any

import psycopg

import paper_engine as dbn
from strategy_runtime import execute_extra_strategies

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.environ["PAPER_SESSION_ID"]
POLL_MS = max(50, int(os.getenv("PAPER_ENGINE_POLL_MS", "250")))
DEFAULT_PROCESS_DELAY_MS = max(1000, int(os.getenv("PAPER_ENGINE_PROCESS_DELAY_MS", "6000")))
STOP = False


def stop(*_: object) -> None:
    global STOP
    STOP = True


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({
        "event": "unified_paper_engine_start",
        "session_id": SESSION_ID,
        "engines": ["DBN", "DSN", "SBK", "HSR", "WTY", "RMO", "PRV", "LVP", "HMF"],
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

            for raw in rows:
                weather = dbn.weather_row(raw)
                try:
                    with conn.transaction():
                        # Deliberate capital priority: proven DBN gets first claim,
                        # then the research strategies use the remaining mode caps.
                        dbn_count = dbn.process_weather(conn, weather)
                        modes = dbn.load_modes(conn)
                        global_cfg = dbn.load_global(conn)
                        extra_count = execute_extra_strategies(
                            conn,
                            SESSION_ID,
                            weather,
                            modes,
                            global_cfg,
                        )
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

    print(json.dumps({"event": "unified_paper_engine_stop", "session_id": SESSION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
