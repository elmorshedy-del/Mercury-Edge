from __future__ import annotations

import hashlib
import html
import json
import os
import re
import signal
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.getenv("PAPER_SESSION_ID", f"paper-{uuid.uuid4()}")
MODEL_VERSION = os.getenv("PAPER_MODEL_VERSION", "paper-v1.1")
POLL_SECONDS = max(2.0, float(os.getenv("IEM_OMO_POLL_SECONDS", "5")))
IEM_BASE = os.getenv("IEM_BASE_URL", "https://mesonet.agron.iastate.edu")

DEFAULT_NETWORKS = {
    "KNYC": "NY_ASOS",
    "KPHL": "PA_ASOS",
    "KLAX": "CA_ASOS",
}


def parse_station_networks() -> dict[str, str]:
    raw = os.getenv("OMO_STATION_NETWORKS", "")
    if not raw:
        return dict(DEFAULT_NETWORKS)
    result: dict[str, str] = {}
    for item in raw.split(","):
        station, sep, network = item.strip().partition(":")
        if not sep or not station or not network:
            raise ValueError(f"bad OMO_STATION_NETWORKS item: {item!r}")
        result[station.upper()] = network
    return result


STATION_NETWORKS = parse_station_networks()
STOP = False
REPORT_RE = re.compile(
    r"(?P<raw>K[A-Z0-9]{3}\s+(?P<day>\d{2})(?P<hour>\d{2})(?P<minute>\d{2})Z[^<\r\n]*?T(?P<temp>M?\d{3,4})(?P<dew>M?\d{3,4})\s+MADISHF)",
    re.IGNORECASE,
)


def stop(*_: object) -> None:
    global STOP
    STOP = True


def utc_iso_from_ns(epoch_ns: int) -> str:
    return datetime.fromtimestamp(epoch_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def temp_token_c(token: str) -> float:
    negative = token.upper().startswith("M")
    digits = token[1:] if negative else token
    value = int(digits) / 10.0
    return -value if negative else value


def c_to_f(value: float) -> float:
    return value * 9 / 5 + 32


def resolve_observed(now_utc: datetime, day: int, hour: int, minute: int) -> datetime:
    candidates: list[datetime] = []
    for delta_month_days in (0, -1, 1):
        probe = now_utc + timedelta(days=delta_month_days * 28)
        try:
            candidate = datetime(probe.year, probe.month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            continue
        candidates.append(candidate)
    if not candidates:
        raise ValueError(f"cannot resolve report day {day}")
    return min(candidates, key=lambda dt: abs((dt - now_utc).total_seconds()))


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "MercuryEdge-Paper/1.1"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


def ensure_session(conn: psycopg.Connection[Any]) -> None:
    config = {
        "omo_station_networks": STATION_NETWORKS,
        "iem_omo_poll_seconds": POLL_SECONDS,
        "omo_collector": "iem-madis-hf-live-v1.1",
    }
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


def collect_station(conn: psycopg.Connection[Any], station: str, network: str) -> int:
    now = datetime.now(timezone.utc)
    date = now.date().isoformat()
    short_station = station[1:] if station.startswith("K") else station
    params = urllib.parse.urlencode({"network": network, "station": short_station, "date": date})
    url = f"{IEM_BASE}/sites/obhistory.php?{params}"
    request_started_ns = time.time_ns()
    request_started_mono_ns = time.monotonic_ns()
    page = fetch_text(url)
    completed_ns = time.time_ns()
    completed_mono_ns = time.monotonic_ns()
    request_rtt_ns = completed_mono_ns - request_started_mono_ns
    decoded = html.unescape(page)
    written = 0

    for match in REPORT_RE.finditer(decoded):
        raw = " ".join(match.group("raw").split())
        if not raw.upper().startswith(station.upper() + " "):
            continue
        observed = resolve_observed(
            datetime.fromtimestamp(completed_ns / 1_000_000_000, tz=timezone.utc),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
        )
        temperature_c = temp_token_c(match.group("temp"))
        dewpoint_c = temp_token_c(match.group("dew"))
        raw_sha = hashlib.sha256(raw.encode()).hexdigest()
        payload = {
            "source_url": url,
            "raw_report": raw,
            "temperature_c": temperature_c,
            "dewpoint_c": dewpoint_c,
            "capture": {
                "request_started_epoch_ns": str(request_started_ns),
                "response_completed_epoch_ns": str(completed_ns),
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
              payload_sha256, compatibility_status, compatibility_rule
            ) VALUES (
              %s,%s,'IEM_MADIS_OMO','OMO',%s,NULL,%s,%s,%s,%s,%s,NULL,%s,%s::jsonb,%s,'unverified',NULL
            )
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (
                SESSION_ID,
                station,
                observed.isoformat(),
                utc_iso_from_ns(completed_ns),
                completed_ns // 1_000_000,
                str(completed_ns),
                str(completed_mono_ns),
                c_to_f(temperature_c),
                raw,
                json.dumps(payload, separators=(",", ":")),
                raw_sha,
            ),
        ).fetchone()
        if row:
            written += 1
    conn.commit()
    return written


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({
        "event": "omo_collector_start",
        "session_id": SESSION_ID,
        "stations": STATION_NETWORKS,
        "poll_seconds": POLL_SECONDS,
    }))
    with psycopg.connect(DATABASE_URL) as conn:
        ensure_session(conn)
        while not STOP:
            cycle_started = time.monotonic()
            for station, network in STATION_NETWORKS.items():
                if STOP:
                    break
                try:
                    count = collect_station(conn, station, network)
                    if count:
                        print(json.dumps({
                            "event": "omo_records",
                            "station": station,
                            "count": count,
                            "session_id": SESSION_ID,
                        }))
                except Exception as exc:
                    conn.rollback()
                    conn.execute(
                        """
                        INSERT INTO audit_findings(
                          session_id,severity,component,finding_code,station_code,details
                        ) VALUES (%s,'warning','weather_iem_omo','IEM_OMO_POLL_FAILED',%s,%s::jsonb)
                        """,
                        (SESSION_ID, station, json.dumps({"error": repr(exc), "network": network})),
                    )
                    conn.commit()
            remaining = max(0.1, POLL_SECONDS - (time.monotonic() - cycle_started))
            deadline = time.monotonic() + remaining
            while not STOP and time.monotonic() < deadline:
                time.sleep(min(0.25, deadline - time.monotonic()))
    print(json.dumps({"event": "omo_collector_stop", "session_id": SESSION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
