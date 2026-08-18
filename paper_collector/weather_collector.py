from __future__ import annotations

import hashlib
import json
import os
import signal
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.getenv("PAPER_SESSION_ID", f"paper-{uuid.uuid4()}")
MODEL_VERSION = os.getenv("PAPER_MODEL_VERSION", "paper-v1")
STATIONS = [x.strip() for x in os.getenv("WEATHER_STATIONS", "KNYC,KPHL,KLAX").split(",") if x.strip()]
POLL_SECONDS = max(60.0, float(os.getenv("AWC_POLL_SECONDS", "60")))
AWC_URL = os.getenv("AWC_METAR_URL", "https://aviationweather.gov/api/data/metar")
STOP = False


def stop(*_: object) -> None:
    global STOP
    STOP = True


def c_to_f(c: float | None) -> float | None:
    return None if c is None else c * 9 / 5 + 32


def iso_from_epoch_seconds(seconds: int | float) -> str:
    return datetime.fromtimestamp(float(seconds), tz=timezone.utc).isoformat()


def http_json(url: str) -> list[dict[str, Any]]:
    req = urllib.request.Request(url, headers={"User-Agent": "MercuryEdge-Paper/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.status == 204:
            return []
        return json.loads(response.read())


def ensure_session(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        """
        INSERT INTO paper_sessions(id, mode, model_version, status, config)
        VALUES (%s,'paper_live',%s,'running',%s::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        (SESSION_ID, MODEL_VERSION, json.dumps({"weather_stations": STATIONS, "awc_poll_seconds": POLL_SECONDS})),
    )
    conn.commit()


def collect_once(conn: psycopg.Connection[Any]) -> int:
    params = urllib.parse.urlencode({"ids": ",".join(STATIONS), "format": "json", "hours": 2})
    url = f"{AWC_URL}?{params}"
    first_seen_ns = time.time_ns()
    first_seen_mono_ns = time.monotonic_ns()
    reports = http_json(url)
    first_seen_ns = time.time_ns()
    first_seen_mono_ns = time.monotonic_ns()
    written = 0

    for report in reports:
        station = report.get("icaoId")
        obs_time = report.get("obsTime")
        raw = report.get("rawOb")
        if not station or obs_time is None or not raw:
            continue
        canonical = json.dumps(report, separators=(",", ":"), sort_keys=True)
        payload_sha = hashlib.sha256(canonical.encode()).hexdigest()
        receipt = report.get("receiptTime")
        temp_c = float(report["temp"]) if report.get("temp") is not None else None
        max_c = float(report["maxT"]) if report.get("maxT") is not None else None

        row = conn.execute(
            """
            INSERT INTO live_weather_journal(
              session_id, station_code, source, report_type,
              observed_at, source_received_at, first_seen_at,
              received_epoch_ms, received_epoch_ns, received_monotonic_ns,
              temperature_f, max_temperature_f, raw_text, raw_payload,
              payload_sha256, compatibility_status, compatibility_rule
            ) VALUES (
              %s,%s,'NOAA_AWC',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'unverified',NULL
            )
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (
                SESSION_ID,
                station,
                str(report.get("metarType") or "METAR"),
                iso_from_epoch_seconds(obs_time),
                str(receipt) if receipt else None,
                datetime.fromtimestamp(first_seen_ns / 1_000_000_000, tz=timezone.utc).isoformat(),
                first_seen_ns // 1_000_000,
                str(first_seen_ns),
                str(first_seen_mono_ns),
                c_to_f(temp_c),
                c_to_f(max_c),
                str(raw),
                canonical,
                payload_sha,
            ),
        ).fetchone()
        if row:
            written += 1
    conn.commit()
    return written


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"event": "weather_collector_start", "session_id": SESSION_ID, "stations": STATIONS}))
    with psycopg.connect(DATABASE_URL) as conn:
        ensure_session(conn)
        while not STOP:
            started = time.monotonic()
            try:
                count = collect_once(conn)
                if count:
                    print(json.dumps({"event": "weather_records", "count": count, "session_id": SESSION_ID}))
            except Exception as exc:
                conn.rollback()
                conn.execute(
                    """
                    INSERT INTO audit_findings(session_id,severity,component,finding_code,details)
                    VALUES (%s,'warning','weather_awc','AWC_POLL_FAILED',%s::jsonb)
                    """,
                    (SESSION_ID, json.dumps({"error": repr(exc)})),
                )
                conn.commit()
            remaining = max(0.1, POLL_SECONDS - (time.monotonic() - started))
            deadline = time.monotonic() + remaining
            while not STOP and time.monotonic() < deadline:
                time.sleep(min(0.5, deadline - time.monotonic()))
    print(json.dumps({"event": "weather_collector_stop", "session_id": SESSION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
