from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
import ssl
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

WS_URL = os.getenv("KALSHI_WS_URL", "wss://external-api-ws.kalshi.com/trade-api/ws/v2")
REST_BASE = os.getenv("KALSHI_REST_BASE", "https://external-api.kalshi.com/trade-api/v2")
SERIES = [x.strip() for x in os.getenv("SERIES_TICKERS", "KXHIGHNY,KXHIGHPHIL,KXHIGHLAX").split(",") if x.strip()]
DISCOVERY_SECONDS = max(5.0, float(os.getenv("MARKET_DISCOVERY_SECONDS", "15")))
REST_CROSSCHECK_SECONDS = max(2.0, float(os.getenv("REST_CROSSCHECK_SECONDS", "10")))
MODEL_VERSION = os.getenv("PAPER_MODEL_VERSION", "paper-v1")
SESSION_ID = os.getenv("PAPER_SESSION_ID", f"paper-{uuid.uuid4()}")
DB_QUEUE_MAX = int(os.getenv("DB_QUEUE_MAX", "100000"))
DB_BATCH_MAX = int(os.getenv("DB_BATCH_MAX", "250"))
DB_BATCH_WAIT_MS = float(os.getenv("DB_BATCH_WAIT_MS", "20"))

DATABASE_URL = os.environ["DATABASE_URL"]
KEY_ID = os.environ["KALSHI_API_KEY_ID"]
PRIVATE_KEY_B64 = os.environ["KALSHI_PRIVATE_KEY_PEM_B64"]

PRIVATE_KEY = serialization.load_pem_private_key(base64.b64decode(PRIVATE_KEY_B64), password=None)
if not isinstance(PRIVATE_KEY, rsa.RSAPrivateKey):
    raise RuntimeError("Kalshi key is not an RSA private key")

stop_event = asyncio.Event()


def utc_iso_from_ns(epoch_ns: int) -> str:
    return datetime.fromtimestamp(epoch_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def payload_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def auth_headers() -> dict[str, str]:
    timestamp = str(time.time_ns() // 1_000_000)
    path = "/trade-api/ws/v2"
    message = f"{timestamp}GET{path}".encode()
    signature = PRIVATE_KEY.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "MercuryEdge-Paper/1.0"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read())


async def discover_markets() -> set[str]:
    tickers: set[str] = set()
    for series in SERIES:
        params = urllib.parse.urlencode({"series_ticker": series, "status": "open", "limit": 1000})
        payload = await asyncio.to_thread(http_json, f"{REST_BASE}/markets?{params}")
        for market in payload.get("markets", []):
            ticker = market.get("ticker")
            if ticker:
                tickers.add(str(ticker))
    return tickers


@dataclass
class JournalRow:
    channel: str
    sid: int | None
    seq: int | None
    market_ticker: str | None
    exchange_ts_ms: int | None
    received_at: str
    received_epoch_ms: int
    received_epoch_ns: int
    received_monotonic_ns: int
    payload: dict[str, Any]
    payload_sha256: str


@dataclass
class AuditRow:
    severity: str
    component: str
    finding_code: str
    market_ticker: str | None
    details: dict[str, Any]


async def insert_session(conn: psycopg.AsyncConnection[Any]) -> None:
    await conn.execute(
        """
        INSERT INTO paper_sessions(id, mode, model_version, status, config)
        VALUES (%s, 'paper_live', %s, 'running', %s::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        (SESSION_ID, MODEL_VERSION, json.dumps({
            "series": SERIES,
            "ws_url": WS_URL,
            "rest_base": REST_BASE,
            "use_yes_price": False,
            "collector": "python-websockets-direct",
        })),
    )
    await conn.commit()


async def db_writer(queue: asyncio.Queue[JournalRow | AuditRow]) -> None:
    conn = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=False)
    try:
        await insert_session(conn)
        while not (stop_event.is_set() and queue.empty()):
            batch: list[JournalRow | AuditRow] = []
            try:
                first = await asyncio.wait_for(queue.get(), timeout=0.5)
                batch.append(first)
            except asyncio.TimeoutError:
                continue

            deadline = time.monotonic() + DB_BATCH_WAIT_MS / 1000
            while len(batch) < DB_BATCH_MAX and time.monotonic() < deadline:
                try:
                    batch.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0)
                    break

            try:
                async with conn.cursor() as cur:
                    for item in batch:
                        if isinstance(item, JournalRow):
                            await cur.execute(
                                """
                                INSERT INTO market_data_journal(
                                  session_id, channel, sid, seq, market_ticker, exchange_ts_ms,
                                  received_at, received_epoch_ms, received_epoch_ns, received_monotonic_ns,
                                  payload, payload_sha256
                                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                                ON CONFLICT DO NOTHING
                                """,
                                (
                                    SESSION_ID, item.channel, item.sid, item.seq, item.market_ticker,
                                    item.exchange_ts_ms, item.received_at, item.received_epoch_ms,
                                    str(item.received_epoch_ns), str(item.received_monotonic_ns),
                                    json.dumps(item.payload, separators=(",", ":")), item.payload_sha256,
                                ),
                            )
                        else:
                            await cur.execute(
                                """
                                INSERT INTO audit_findings(
                                  session_id, severity, component, finding_code, market_ticker, details
                                ) VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                                """,
                                (SESSION_ID, item.severity, item.component, item.finding_code,
                                 item.market_ticker, json.dumps(item.details, separators=(",", ":"))),
                            )
                await conn.commit()
            except Exception:
                await conn.rollback()
                # The event journal is the evidence base. If persistence fails,
                # fail the collector rather than silently dropping market data.
                stop_event.set()
                raise
            finally:
                for _ in batch:
                    queue.task_done()
    finally:
        try:
            await conn.execute(
                "UPDATE paper_sessions SET stopped_at=now(), status=%s WHERE id=%s",
                ("stopped" if stop_event.is_set() else "failed", SESSION_ID),
            )
            await conn.commit()
        except Exception:
            pass
        await conn.close()


async def journal_ws(queue: asyncio.Queue[JournalRow | AuditRow], raw_text: str) -> dict[str, Any]:
    wall_ns = time.time_ns()
    mono_ns = time.monotonic_ns()
    raw_bytes = raw_text.encode()
    data = json.loads(raw_text)
    msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
    exchange_ts = msg.get("ts_ms")
    ticker = msg.get("market_ticker")
    row = JournalRow(
        channel=str(data.get("type", "unknown")),
        sid=int(data["sid"]) if data.get("sid") is not None else None,
        seq=int(data["seq"]) if data.get("seq") is not None else None,
        market_ticker=str(ticker) if ticker is not None else None,
        exchange_ts_ms=int(exchange_ts) if exchange_ts is not None else None,
        received_at=utc_iso_from_ns(wall_ns),
        received_epoch_ms=wall_ns // 1_000_000,
        received_epoch_ns=wall_ns,
        received_monotonic_ns=mono_ns,
        payload=data,
        payload_sha256=payload_hash(raw_bytes),
    )
    await queue.put(row)
    return data


async def journal_rest_orderbook(queue: asyncio.Queue[JournalRow | AuditRow], ticker: str) -> None:
    url = f"{REST_BASE}/markets/{urllib.parse.quote(ticker, safe='')}/orderbook?depth=0"
    before_ns = time.time_ns()
    before_mono = time.monotonic_ns()
    payload = await asyncio.to_thread(http_json, url)
    after_ns = time.time_ns()
    after_mono = time.monotonic_ns()
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    await queue.put(JournalRow(
        channel="rest_orderbook_crosscheck",
        sid=None,
        seq=None,
        market_ticker=ticker,
        exchange_ts_ms=None,
        received_at=utc_iso_from_ns(after_ns),
        received_epoch_ms=after_ns // 1_000_000,
        received_epoch_ns=after_ns,
        received_monotonic_ns=after_mono,
        payload={"request_started_epoch_ns": str(before_ns), "request_rtt_ns": str(after_mono - before_mono), "response": payload},
        payload_sha256=payload_hash(canonical),
    ))


async def rest_crosscheck_loop(queue: asyncio.Queue[JournalRow | AuditRow], active_ref: dict[str, set[str]]) -> None:
    while not stop_event.is_set():
        start = time.monotonic()
        markets = sorted(active_ref["markets"])
        if markets:
            # Bound concurrency to avoid self-induced burst latency and rate-limit noise.
            sem = asyncio.Semaphore(5)

            async def one(ticker: str) -> None:
                async with sem:
                    try:
                        await journal_rest_orderbook(queue, ticker)
                    except Exception as exc:
                        await queue.put(AuditRow("warning", "kalshi_rest", "REST_ORDERBOOK_CROSSCHECK_FAILED", ticker, {"error": repr(exc)}))

            await asyncio.gather(*(one(t) for t in markets))
        sleep_for = max(0.1, REST_CROSSCHECK_SECONDS - (time.monotonic() - start))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass


async def run_ws(queue: asyncio.Queue[JournalRow | AuditRow], active_ref: dict[str, set[str]]) -> None:
    reconnect_backoff = 0.5
    while not stop_event.is_set():
        markets = await discover_markets()
        active_ref["markets"] = set(markets)
        if not markets:
            await queue.put(AuditRow("warning", "kalshi_ws", "NO_OPEN_MARKETS", None, {"series": SERIES}))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
            continue

        headers = auth_headers()
        try:
            async with websockets.connect(
                WS_URL,
                additional_headers=headers,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                compression=None,
                max_queue=8192,
                max_size=4 * 1024 * 1024,
            ) as ws:
                reconnect_backoff = 0.5
                subscribe = {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": sorted(markets),
                        # Explicit legacy own-leg NO pricing. Do not depend on
                        # Kalshi's announced future default flip.
                        "use_yes_price": False,
                    },
                }
                await ws.send(json.dumps(subscribe, separators=(",", ":")))

                orderbook_sid: int | None = None
                last_seq: dict[int, int] = {}
                current_markets = set(markets)
                next_discovery = time.monotonic() + DISCOVERY_SECONDS

                while not stop_event.is_set():
                    timeout = max(0.05, next_discovery - time.monotonic())
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        raw = None

                    if raw is not None:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        data = await journal_ws(queue, raw)
                        msg_type = data.get("type")
                        if msg_type == "error":
                            await queue.put(AuditRow("error", "kalshi_ws", "WS_ERROR_MESSAGE", None, {"message": data}))
                        elif msg_type == "subscribed" and data.get("msg", {}).get("channel") == "orderbook_delta":
                            orderbook_sid = int(data["msg"]["sid"])
                        elif msg_type in ("orderbook_snapshot", "orderbook_delta"):
                            sid = int(data.get("sid")) if data.get("sid") is not None else None
                            seq = int(data.get("seq")) if data.get("seq") is not None else None
                            if sid is not None and seq is not None:
                                prev = last_seq.get(sid)
                                if prev is not None and seq != prev + 1:
                                    ticker = data.get("msg", {}).get("market_ticker")
                                    await queue.put(AuditRow(
                                        "fatal", "kalshi_ws", "ORDERBOOK_SEQUENCE_GAP", ticker,
                                        {"sid": sid, "expected": prev + 1, "received": seq,
                                         "missing_from": prev + 1, "missing_to": seq - 1},
                                    ))
                                    # The raw stream has permanent missing evidence.
                                    # Re-snapshot every tracked market to restore a
                                    # known-good state for subsequent paper signals.
                                    if orderbook_sid is not None and current_markets:
                                        await ws.send(json.dumps({
                                            "id": int(time.time_ns() % 2_000_000_000),
                                            "cmd": "update_subscription",
                                            "params": {
                                                "sids": [orderbook_sid],
                                                "market_tickers": sorted(current_markets),
                                                "action": "get_snapshot",
                                            },
                                        }, separators=(",", ":")))
                                last_seq[sid] = seq

                    if time.monotonic() >= next_discovery:
                        discovered = await discover_markets()
                        active_ref["markets"] = set(discovered)
                        if orderbook_sid is not None:
                            add = sorted(discovered - current_markets)
                            delete = sorted(current_markets - discovered)
                            if add:
                                await ws.send(json.dumps({
                                    "id": int(time.time_ns() % 2_000_000_000),
                                    "cmd": "update_subscription",
                                    "params": {"sids": [orderbook_sid], "market_tickers": add, "action": "add_markets"},
                                }, separators=(",", ":")))
                            if delete:
                                await ws.send(json.dumps({
                                    "id": int(time.time_ns() % 2_000_000_000),
                                    "cmd": "update_subscription",
                                    "params": {"sids": [orderbook_sid], "market_tickers": delete, "action": "delete_markets"},
                                }, separators=(",", ":")))
                        current_markets = set(discovered)
                        next_discovery = time.monotonic() + DISCOVERY_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(AuditRow("error", "kalshi_ws", "WS_CONNECTION_RESET", None, {"error": repr(exc), "backoff_seconds": reconnect_backoff}))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=reconnect_backoff)
            except asyncio.TimeoutError:
                pass
            reconnect_backoff = min(15.0, reconnect_backoff * 2)


async def main() -> None:
    print(json.dumps({"event": "paper_collector_start", "session_id": SESSION_ID, "series": SERIES}))
    queue: asyncio.Queue[JournalRow | AuditRow] = asyncio.Queue(maxsize=DB_QUEUE_MAX)
    active_ref: dict[str, set[str]] = {"markets": set()}

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    writer = asyncio.create_task(db_writer(queue), name="db-writer")
    ws_task = asyncio.create_task(run_ws(queue, active_ref), name="kalshi-ws")
    rest_task = asyncio.create_task(rest_crosscheck_loop(queue, active_ref), name="rest-crosscheck")

    done, pending = await asyncio.wait({writer, ws_task, rest_task}, return_when=asyncio.FIRST_EXCEPTION)
    for task in done:
        exc = task.exception()
        if exc:
            print(json.dumps({"event": "paper_collector_fatal", "task": task.get_name(), "error": repr(exc)}))
            stop_event.set()
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await queue.join()
    print(json.dumps({"event": "paper_collector_stop", "session_id": SESSION_ID}))


if __name__ == "__main__":
    asyncio.run(main())
