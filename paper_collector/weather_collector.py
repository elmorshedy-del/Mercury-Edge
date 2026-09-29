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

from raw_journal import RawCapture, insert_raw_capture

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.getenv("PAPER_SESSION_ID", f"paper-{uuid.uuid4()}")
MODEL_VERSION = os.getenv("PAPER_MODEL_VERSION", "paper-v1.1")
STATIONS = [x.strip() for x in os.getenv("WEATHER_STATIONS", "KNYC,KPHL,KLAX").split(",") if x.strip()]
POLL_SECONDS = max(5.0, float(os.getenv("AWC_POLL_SECONDS", "10")))
AWC_URL = os.getenv("AWC_METAR_URL", "https://aviationweather.gov/api/data/metar")
STOP = False


def stop(*_: object) -> None:
    global STOP
    STOP = True


def c_to_f(c: float | None) -> float | None:
    return None if c is None else c * 9 / 5 + 32


def iso_from_epoch_seconds(seconds: int | float) -> str:
    return datetime.fromtimestamp(float(seconds), tz=timezone.utc).isoformat()


def http_json_capture(url: str) -> tuple[list[dict[str, Any]], bytes, dict[str, Any]]:
    """Return parsed JSON plus the exact HTTP entity bytes used to parse it."""
    req = urllib.request.Request(url, headers={"User-Agent": "MercuryEdge-Paper/1.1"})
    with urllib.request.urlopen(req, timeout=15) as response:
        raw_bytes = response.read()
        metadata = {
            "status": int(response.status),
            "content_type": response.headers.get("Content-Type"),
            "content_encoding": response.headers.get("Content-Encoding"),
            "date": response.headers.get("Date"),
        }
        if response.status == 204 or not raw_bytes:
            return [], raw_bytes, metadata
        decoded = json.loads(raw_bytes)
        if not isinstance(decoded, list):
            raise ValueError("AWC METAR response is not a JSON list")
        return decoded, raw_bytes, metadata


def ensure_session(conn: psycopg.Connection[Any]) -> None:
    config = {"weather_stations": STATIONS, "awc_poll_seconds": POLL_SECONDS, "weather_collector": "awc-live-v1.1"}
    conn.execute(
        """
        INSERT INTO paper_sessions(id, mode, model_version, status, config)
        VALUES (%s,'paper_live',%s,'running',%s::jsonb)
        ON CONFLICT (id) DO UPDATE SET
          model_version=EXCLUDED.model_version,
          status='running',
          config=paper_sessions.config || EXCLUDED.config
        """,
        (SESSION_ID, MODEL_VERSION, json.dumps(config, separators=(",", ":"))),
    )
    conn.commit()


def collect_once(conn: psycopg.Connection[Any]) -> int:
    params = urllib.parse.urlencode({"ids": ",".join(STATIONS), "format": "json", "hours": 2})
    url = f"{AWC_URL}?{params}"
    request_started_ns = time.time_ns()
    request_started_mono_ns = time.monotonic_ns()
    reports, raw_response_bytes, response_metadata = http_json_capture(url)
    response_completed_ns = time.time_ns()
    response_completed_mono_ns = time.monotonic_ns()
    request_rtt_ns = response_completed_mono_ns - request_started_mono_ns
    received_at = datetime.fromtimestamp(response_completed_ns / 1_000_000_000, tz=timezone.utc)

    # Preserve the source body before any report-level normalization. Every
    # parsed live_weather_journal row below points back to this immutable batch.
    raw_source_id = insert_raw_capture(
        conn,
        RawCapture(
            session_id=SESSION_ID,
            source="NOAA_AWC",
            source_stream="metar_json_batch",
            raw_bytes=raw_response_bytes,
            received_at=received_at,
            received_epoch_ns=response_completed_ns,
            received_monotonic_ns=response_completed_mono_ns,
            transport="https_poll",
            content_type=str(response_metadata.get("content_type") or "application/json"),
            content_encoding=(
                str(response_metadata["content_encoding"])
                if response_metadata.get("content_encoding")
                else None
            ),
            metadata={
                "collector": "awc-live-v1.1",
                "request_started_epoch_ns": str(request_started_ns),
                "request_started_monotonic_ns": str(request_started_mono_ns),
                "request_rtt_ns": str(request_rtt_ns),
                "http": response_metadata,
                "requested_stations": STATIONS,
                "hours": 2,
                "format": "json",
            },
        ),
    )

    written = 0
    for report in reports:
        station = report.get("icaoId")
        obs_time = report.get("obsTime")
        raw = report.get("rawOb")
        if not station or obs_time is None or not raw:
            continue
        canonical = json.dumps(report, separators=(",", ":"), sort_keys=True)
        payload_sha = hashlib.sha256(canonical.encode()).hexdigest()
        source_receipt = report.get("receiptTime")
        temp_c = float(report["temp"]) if report.get("temp") is not None else None
        max_c = float(report["maxT"]) if report.get("maxT") is not None else None
        stored_payload = {
            "report": report,
            "capture": {
                "raw_source_id": raw_source_id,
                "request_started_epoch_ns": str(request_started_ns),
                "response_completed_epoch_ns": str(response_completed_ns),
                "request_rtt_ns": str(request_rtt_ns),
            },
        }

        row = conn.execute(
            """
            INSERT INTO live_weather_journal(
              session_id, station_code, source, report_type,
              observed_at, source_received_at, first_seen_at,
              received_epoch_ms, received_epoch_ns, received_monotonic_ns,
              temperature_f, max_temperature_f, raw_text, raw_payload,
              payload_sha256, compatibility_status, compatibility_rule,raw_source_id
            ) VALUES (
              %s,%s,'NOAA_AWC',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'unverified',NULL,%s
            )
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (
                SESSION_ID,
                station,
                str(report.get("metarType") or "METAR"),
                iso_from_epoch_seconds(obs_time),
                str(source_receipt) if source_receipt else None,
                received_at.isoformat(),
                response_completed_ns // 1_000_000,
                str(response_completed_ns),
                str(response_completed_mono_ns),
                c_to_f(temp_c),
                c_to_f(max_c),
                str(raw),
                json.dumps(stored_payload, separators=(",", ":")),
                payload_sha,
                raw_source_id,
            ),
        ).fetchone()
        if row:
            written += 1
    conn.commit()
    return written


def utc_iso_from_ns(epoch_ns: int) -> str:
    return datetime.fromtimestamp(epoch_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({
        "event": "weather_collector_start",
        "session_id": SESSION_ID,
        "stations": STATIONS,
        "poll_seconds": POLL_SECONDS,
    }))
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
