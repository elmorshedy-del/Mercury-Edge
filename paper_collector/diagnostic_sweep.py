from __future__ import annotations

"""Periodic Step 4I-B diagnostic sweep; downstream of benchmark decisions."""

import json
import os

import psycopg

from failure_event_sweeper import sweep_failure_events

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.getenv("AUDIT_SESSION_ID")


def _resolve_session(conn: psycopg.Connection) -> str:
    if SESSION_ID:
        return SESSION_ID
    row = conn.execute(
        "SELECT id FROM paper_sessions WHERE mode='paper_live' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("no paper-live session found for diagnostic sweep")
    return str(row[0])


def main() -> int:
    with psycopg.connect(DATABASE_URL) as conn:
        session_id = _resolve_session(conn)
        derived = sweep_failure_events(conn, session_id=session_id)
        conn.commit()
    print(json.dumps({
        "event": "hard_edge_diagnostic_sweep_complete",
        "session_id": session_id,
        "derived_events": derived,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
