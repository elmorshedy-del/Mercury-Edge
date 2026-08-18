from __future__ import annotations

import json
import os
import signal
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.environ["PAPER_SESSION_ID"]
REST_BASE = os.getenv("KALSHI_REST_BASE", "https://external-api.kalshi.com/trade-api/v2")
POLL_SECONDS = max(15.0, float(os.getenv("PAPER_SETTLEMENT_POLL_SECONDS", "30")))
BATCH_SIZE = max(1, min(100, int(os.getenv("PAPER_SETTLEMENT_BATCH_SIZE", "100"))))
STOP = False


def stop(*_: object) -> None:
    global STOP
    STOP = True


def http_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "MercuryEdge-Paper/2.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def tracked_tickers(conn: psycopg.Connection[Any]) -> list[str]:
    tickers = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT market_ticker
              FROM paper_positions p
              JOIN paper_portfolios pf ON pf.id=p.portfolio_id
             WHERE pf.session_id=%s AND p.status='open'
            """,
            (SESSION_ID,),
        ).fetchall()
        if row[0]
    }
    rows = conn.execute(
        """
        SELECT details
          FROM paper_shadow_trials
         WHERE session_id=%s AND status='filled'
        """,
        (SESSION_ID,),
    ).fetchall()
    for (details,) in rows:
        payload = details if isinstance(details, dict) else {}
        legs = payload.get("legs") if isinstance(payload.get("legs"), list) else []
        for leg in legs:
            if isinstance(leg, dict) and leg.get("market_ticker"):
                tickers.add(str(leg["market_ticker"]))
    return sorted(tickers)


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def collect_once(conn: psycopg.Connection[Any]) -> int:
    tickers = tracked_tickers(conn)
    written = 0
    for batch in chunks(tickers, BATCH_SIZE):
        params = urllib.parse.urlencode({"tickers": ",".join(batch), "limit": len(batch)})
        payload = http_json(f"{REST_BASE}/markets?{params}")
        for market in payload.get("markets") or []:
            if not isinstance(market, dict):
                continue
            ticker = str(market.get("ticker") or "")
            if not ticker:
                continue
            result = str(market.get("result") or "").lower() or None
            if result not in (None, "yes", "no", "scalar"):
                result = None
            status = str(market.get("status") or "unknown").lower()
            settlement_ts = market.get("settlement_ts")
            conn.execute(
                """
                INSERT INTO paper_market_results(
                  market_ticker,event_ticker,market_status,result,is_provisional,
                  settlement_ts,checked_at,raw_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,now(),%s::jsonb)
                ON CONFLICT (market_ticker) DO UPDATE SET
                  event_ticker=EXCLUDED.event_ticker,
                  market_status=EXCLUDED.market_status,
                  result=EXCLUDED.result,
                  is_provisional=EXCLUDED.is_provisional,
                  settlement_ts=EXCLUDED.settlement_ts,
                  checked_at=now(),
                  raw_payload=EXCLUDED.raw_payload
                """,
                (
                    ticker,
                    str(market.get("event_ticker") or "") or None,
                    status,
                    result,
                    bool(market.get("is_provisional")) if market.get("is_provisional") is not None else None,
                    settlement_ts,
                    json.dumps(market, separators=(",", ":")),
                ),
            )
            written += 1
    conn.commit()
    return written


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({
        "event": "paper_settlement_collector_start",
        "session_id": SESSION_ID,
        "poll_seconds": POLL_SECONDS,
    }))
    with psycopg.connect(DATABASE_URL) as conn:
        while not STOP:
            started = time.monotonic()
            try:
                count = collect_once(conn)
                if count:
                    finalized = conn.execute(
                        "SELECT count(*) FROM paper_market_results WHERE market_status='finalized' AND result IN ('yes','no')"
                    ).fetchone()
                    print(json.dumps({
                        "event": "paper_settlement_check",
                        "markets": count,
                        "finalized": int(finalized[0] or 0) if finalized else 0,
                    }), flush=True)
            except Exception as exc:
                conn.rollback()
                print(json.dumps({
                    "event": "paper_settlement_collector_error",
                    "error": repr(exc),
                }), flush=True)
            remaining = max(0.1, POLL_SECONDS - (time.monotonic() - started))
            deadline = time.monotonic() + remaining
            while not STOP and time.monotonic() < deadline:
                time.sleep(min(0.5, deadline - time.monotonic()))
    print(json.dumps({"event": "paper_settlement_collector_stop", "session_id": SESSION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
