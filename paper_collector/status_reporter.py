from __future__ import annotations

import json
import os
import signal
import time
from typing import Any

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.environ["PAPER_SESSION_ID"]
INTERVAL_SECONDS = max(15, int(os.getenv("PAPER_STATUS_INTERVAL_SECONDS", "30")))
STOP = False


def stop(*_: object) -> None:
    global STOP
    STOP = True


def scalar(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def snapshot(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    ws = conn.execute(
        """
        SELECT finding_code,detected_at,details
          FROM audit_findings
         WHERE session_id=%s AND component='kalshi_ws'
         ORDER BY id DESC LIMIT 5
        """,
        (SESSION_ID,),
    ).fetchall()
    journal = conn.execute(
        """
        SELECT channel,count(*)::int
          FROM market_data_journal
         WHERE session_id=%s
         GROUP BY channel ORDER BY channel
        """,
        (SESSION_ID,),
    ).fetchall()
    signals = conn.execute(
        """
        SELECT strategy_code,auditor_status,count(*)::int
          FROM paper_signals
         WHERE session_id=%s
         GROUP BY strategy_code,auditor_status
         ORDER BY strategy_code,auditor_status
        """,
        (SESSION_ID,),
    ).fetchall()
    orders = conn.execute(
        """
        SELECT strategy_code,status,count(*)::int
          FROM paper_orders
         WHERE session_id=%s
         GROUP BY strategy_code,status
         ORDER BY strategy_code,status
        """,
        (SESSION_ID,),
    ).fetchall()
    portfolios = conn.execute(
        """
        SELECT mode_code,starting_bankroll,cash_balance,reserved_cash,realized_pnl,status
          FROM paper_portfolios
         WHERE session_id=%s
         ORDER BY mode_code
        """,
        (SESSION_ID,),
    ).fetchall()
    replay = conn.execute(
        """
        SELECT status,journal_rows,integrity_failures,sequence_gaps,book_errors,finished_at
          FROM paper_replay_runs
         WHERE session_id=%s
         ORDER BY id DESC LIMIT 1
        """,
        (SESSION_ID,),
    ).fetchone()
    return {
        "event": "paper_runtime_status",
        "session_id": SESSION_ID,
        "market_rows": sum(int(count) for _, count in journal),
        "market_tickers": scalar(conn, "SELECT count(DISTINCT market_ticker) FROM market_data_journal WHERE session_id=%s AND market_ticker IS NOT NULL", (SESSION_ID,)),
        "connections": scalar(conn, "SELECT count(DISTINCT connection_id) FROM market_data_journal WHERE session_id=%s AND connection_id IS NOT NULL", (SESSION_ID,)),
        "channels": {str(channel): int(count) for channel, count in journal},
        "ws_connected": any(str(code) == "WS_CONNECTED" for code, _, _ in ws),
        "ws_findings": [
            {"code": str(code), "at": at.isoformat(), "details": details if isinstance(details, dict) else {}}
            for code, at, details in ws
        ],
        "signals": [
            {"strategy": str(strategy), "auditor": str(auditor), "count": int(count)}
            for strategy, auditor, count in signals
        ],
        "orders": [
            {"strategy": str(strategy), "status": str(status), "count": int(count)}
            for strategy, status, count in orders
        ],
        "portfolios": [
            {
                "mode": str(mode), "start": float(start), "cash": float(cash),
                "reserved": float(reserved), "realized_pnl": float(pnl), "status": str(status),
            }
            for mode, start, cash, reserved, pnl, status in portfolios
        ],
        "replay": None if replay is None else {
            "status": str(replay[0]), "journal_rows": int(replay[1]),
            "integrity_failures": int(replay[2]), "sequence_gaps": int(replay[3]),
            "book_errors": int(replay[4]), "finished_at": replay[5].isoformat() if replay[5] else None,
        },
        "real_money": False,
    }


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with psycopg.connect(DATABASE_URL) as conn:
        while not STOP:
            try:
                print(json.dumps(snapshot(conn), separators=(",", ":"), default=str), flush=True)
            except Exception as exc:
                conn.rollback()
                print(json.dumps({"event": "paper_runtime_status_error", "error": repr(exc)}), flush=True)
            deadline = time.monotonic() + INTERVAL_SECONDS
            while not STOP and time.monotonic() < deadline:
                time.sleep(min(0.5, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
