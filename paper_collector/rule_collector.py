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
MODEL_VERSION = os.getenv("PAPER_MODEL_VERSION", "paper-v1.1")
REST_BASE = os.getenv("KALSHI_REST_BASE", "https://external-api.kalshi.com/trade-api/v2")
SERIES = [x.strip() for x in os.getenv("SERIES_TICKERS", "KXHIGHNY,KXHIGHPHIL,KXHIGHLAX").split(",") if x.strip()]
POLL_SECONDS = max(60.0, float(os.getenv("RULE_POLL_SECONDS", "300")))
STOP = False


def stop(*_: object) -> None:
    global STOP
    STOP = True


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "MercuryEdge-Paper/1.1"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read())


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def ensure_session(conn: psycopg.Connection[Any]) -> None:
    config = {"rule_poll_seconds": POLL_SECONDS, "rule_collector": "kalshi-rules-v1.1"}
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


def sources(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            result.append({"name": str(item.get("name") or ""), "url": str(item.get("url") or "")})
    return result


def insert_snapshot(
    conn: psycopg.Connection[Any],
    *,
    series_ticker: str,
    event_ticker: str | None,
    settlement_sources: list[dict[str, str]],
    fee_type: str | None,
    fee_multiplier: float | None,
    important_info: Any,
    material: Any,
    raw_payload: Any,
) -> int:
    rules_hash = canonical_hash(material)
    row = conn.execute(
        """
        INSERT INTO settlement_rule_snapshots(
          session_id, series_ticker, event_ticker, captured_at, rules_hash,
          settlement_sources, fee_type, fee_multiplier, important_info, raw_payload
        ) VALUES (%s,%s,%s,now(),%s,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        (
            SESSION_ID,
            series_ticker,
            event_ticker,
            rules_hash,
            json.dumps(settlement_sources, separators=(",", ":")),
            fee_type,
            fee_multiplier,
            json.dumps(important_info, separators=(",", ":")),
            json.dumps(raw_payload, separators=(",", ":")),
        ),
    ).fetchone()
    return 1 if row else 0


def collect_series(conn: psycopg.Connection[Any], series_ticker: str) -> tuple[int, list[str]]:
    series_payload = http_json(f"{REST_BASE}/series/{urllib.parse.quote(series_ticker, safe='')}")
    series = series_payload.get("series") if isinstance(series_payload.get("series"), dict) else {}
    settlement_sources = sources(series.get("settlement_sources"))
    product_metadata = series.get("product_metadata") if isinstance(series.get("product_metadata"), dict) else {}
    important_info = product_metadata.get("important_info")
    fee_type = str(series.get("fee_type")) if series.get("fee_type") is not None else None
    fee_multiplier_raw = series.get("fee_multiplier")
    fee_multiplier = float(fee_multiplier_raw) if fee_multiplier_raw is not None else None
    material = {
        "ticker": series.get("ticker"),
        "settlement_sources": settlement_sources,
        "contract_terms_url": series.get("contract_terms_url"),
        "contract_url": series.get("contract_url"),
        "fee_type": fee_type,
        "fee_multiplier": fee_multiplier,
        "important_info": important_info,
        "last_updated_ts": series.get("last_updated_ts"),
    }
    written = insert_snapshot(
        conn,
        series_ticker=series_ticker,
        event_ticker=None,
        settlement_sources=settlement_sources,
        fee_type=fee_type,
        fee_multiplier=fee_multiplier,
        important_info=important_info,
        material=material,
        raw_payload=series_payload,
    )

    params = urllib.parse.urlencode({"series_ticker": series_ticker, "status": "open", "limit": 200})
    events_payload = http_json(f"{REST_BASE}/events?{params}")
    event_tickers = [
        str(event.get("event_ticker"))
        for event in events_payload.get("events", [])
        if isinstance(event, dict) and event.get("event_ticker")
    ]
    return written, event_tickers


def collect_event(conn: psycopg.Connection[Any], event_ticker: str) -> int:
    url = f"{REST_BASE}/events/{urllib.parse.quote(event_ticker, safe='')}?with_nested_markets=true"
    payload = http_json(url)
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    series_ticker = str(event.get("series_ticker") or "")
    if not series_ticker:
        raise RuntimeError(f"event {event_ticker} missing series_ticker")
    settlement_sources = sources(event.get("settlement_sources"))
    markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    market_material = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        market_material.append({
            "ticker": market.get("ticker"),
            "floor_strike": market.get("floor_strike"),
            "cap_strike": market.get("cap_strike"),
            "strike_type": market.get("strike_type"),
            "rules_primary": market.get("rules_primary"),
            "price_level_structure": market.get("price_level_structure"),
        })
    material = {
        "event_ticker": event.get("event_ticker"),
        "series_ticker": series_ticker,
        "settlement_sources": settlement_sources,
        "mutually_exclusive": event.get("mutually_exclusive"),
        "markets": sorted(market_material, key=lambda x: str(x.get("ticker") or "")),
    }
    return insert_snapshot(
        conn,
        series_ticker=series_ticker,
        event_ticker=event_ticker,
        settlement_sources=settlement_sources,
        fee_type=None,
        fee_multiplier=None,
        important_info={"mutually_exclusive": event.get("mutually_exclusive")},
        material=material,
        raw_payload=payload,
    )


def collect_once(conn: psycopg.Connection[Any]) -> int:
    written = 0
    event_tickers: set[str] = set()
    for series_ticker in SERIES:
        count, events = collect_series(conn, series_ticker)
        written += count
        event_tickers.update(events)
    for event_ticker in sorted(event_tickers):
        written += collect_event(conn, event_ticker)
    conn.commit()
    return written


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"event": "rule_collector_start", "session_id": SESSION_ID, "series": SERIES}))
    with psycopg.connect(DATABASE_URL) as conn:
        ensure_session(conn)
        while not STOP:
            started = time.monotonic()
            try:
                count = collect_once(conn)
                if count:
                    print(json.dumps({"event": "rule_snapshots_written", "count": count, "session_id": SESSION_ID}))
            except Exception as exc:
                conn.rollback()
                conn.execute(
                    """
                    INSERT INTO audit_findings(session_id,severity,component,finding_code,details)
                    VALUES (%s,'error','rules','RULE_SNAPSHOT_FAILED',%s::jsonb)
                    """,
                    (SESSION_ID, json.dumps({"error": repr(exc)})),
                )
                conn.commit()
            remaining = max(0.1, POLL_SECONDS - (time.monotonic() - started))
            deadline = time.monotonic() + remaining
            while not STOP and time.monotonic() < deadline:
                time.sleep(min(0.5, deadline - time.monotonic()))
    print(json.dumps({"event": "rule_collector_stop", "session_id": SESSION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
