from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
import time
import urllib.error
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
# Open daily-temperature market universes change slowly. Five-minute discovery
# keeps subscriptions current without burning REST quota while the WS stays hot.
DISCOVERY_SECONDS = max(30.0, float(os.getenv("MARKET_DISCOVERY_SECONDS", "300")))
DISCOVERY_REQUEST_GAP_SECONDS = max(0.05, float(os.getenv("MARKET_DISCOVERY_REQUEST_GAP_SECONDS", "0.30")))
# REST full-book calls are audit cross-checks, never the trading data path.
REST_CROSSCHECK_SECONDS = max(30.0, float(os.getenv("REST_CROSSCHECK_SECONDS", "300")))
MODEL_VERSION = os.getenv("PAPER_MODEL_VERSION", "paper-v1.2")
SESSION_ID = os.getenv("PAPER_SESSION_ID", f"paper-{uuid.uuid4()}")
DB_QUEUE_MAX = int(os.getenv("DB_QUEUE_MAX", "100000"))
DB_BATCH_MAX = int(os.getenv("DB_BATCH_MAX", "250"))
DB_BATCH_WAIT_MS = float(os.getenv("DB_BATCH_WAIT_MS", "20"))
PRICE_MODE = "unified_yes"
USE_YES_PRICE = True

DATABASE_URL = os.environ["DATABASE_URL"]
KEY_ID = os.environ["KALSHI_API_KEY_ID"]
PRIVATE_KEY_B64 = os.environ["KALSHI_PRIVATE_KEY_PEM_B64"]

PRIVATE_KEY = serialization.load_pem_private_key(base64.b64decode(PRIVATE_KEY_B64), password=None)
if not isinstance(PRIVATE_KEY, rsa.RSAPrivateKey):
    raise RuntimeError("Kalshi key is not an RSA private key")
PUBLIC_DER = PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.DER,
    serialization.PublicFormat.SubjectPublicKeyInfo,
)
KEY_FINGERPRINT = hashlib.sha256(PUBLIC_DER).hexdigest()[:24]

stop_event = asyncio.Event()


class SequenceGap(RuntimeError):
    pass


class MissingSnapshot(RuntimeError):
    pass


class MarketUniverseChanged(RuntimeError):
    pass


def utc_iso_from_ns(epoch_ns: int) -> str:
    return datetime.fromtimestamp(epoch_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def payload_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def next_chain_hash(prev: str | None, connection_id: str, received_epoch_ns: int, raw_sha: str) -> str:
    material = f"{prev or ''}|{connection_id}|{received_epoch_ns}|{raw_sha}".encode()
    return hashlib.sha256(material).hexdigest()


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


def http_json(url: str, *, attempts: int = 6) -> dict[str, Any]:
    """GET JSON with bounded retry for rate limits and transient server errors.

    This runs in a worker thread, so Retry-After sleeps never block the WS receive
    loop. Authentication/signing is not used for these public REST reads.
    """
    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": "MercuryEdge-Paper/1.2"})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                server_delay = float(retry_after) if retry_after else 0.0
            except (TypeError, ValueError):
                server_delay = 0.0
            sleep_for = max(server_delay, delay)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            sleep_for = delay
        if attempt + 1 >= attempts:
            break
        time.sleep(min(60.0, sleep_for))
        delay = min(30.0, delay * 2)
    if last_error is not None:
        raise last_error
    raise RuntimeError("HTTP request failed without an exception")


async def discover_markets() -> set[str]:
    """Discover all open markets without an 18-request burst or restart storm."""
    backoff = 2.0
    while not stop_event.is_set():
        tickers: set[str] = set()
        try:
            for index, series in enumerate(SERIES):
                if index:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=DISCOVERY_REQUEST_GAP_SECONDS)
                        return set()
                    except asyncio.TimeoutError:
                        pass
                params = urllib.parse.urlencode({"series_ticker": series, "status": "open", "limit": 1000})
                payload = await asyncio.to_thread(http_json, f"{REST_BASE}/markets?{params}")
                tickers.update(str(m["ticker"]) for m in payload.get("markets", []) if m.get("ticker"))
            return tickers
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Discovery is necessary for a clean subscription universe, but a
            # transient REST throttle must never crash/restart the whole service.
            print(json.dumps({
                "event": "market_discovery_retry",
                "error": repr(exc),
                "backoff_seconds": backoff,
            }))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                return set()
            except asyncio.TimeoutError:
                pass
            backoff = min(60.0, backoff * 2)
    return set()


async def discover_after_delay() -> set[str]:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=DISCOVERY_SECONDS)
        return set()
    except asyncio.TimeoutError:
        return await discover_markets()


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
    raw_text: str
    payload: dict[str, Any]
    payload_sha256: str
    connection_id: str | None = None
    prev_chain_hash: str | None = None
    chain_hash: str | None = None
    price_mode: str | None = None
    source_message_type: str | None = None


@dataclass
class AuditRow:
    severity: str
    component: str
    finding_code: str
    market_ticker: str | None
    details: dict[str, Any]


@dataclass
class ConnectionJournalState:
    connection_id: str
    prev_chain_hash: str | None = None


async def insert_session(conn: psycopg.AsyncConnection[Any]) -> None:
    config = {
        "series": SERIES,
        "ws_url": WS_URL,
        "rest_base": REST_BASE,
        "use_yes_price": USE_YES_PRICE,
        "price_mode": PRICE_MODE,
        "collector": "python-websockets-direct-v1.2",
        "key_fingerprint": KEY_FINGERPRINT,
        "channels": ["orderbook_delta", "trade"],
        "market_discovery_seconds": DISCOVERY_SECONDS,
        "market_discovery_request_gap_seconds": DISCOVERY_REQUEST_GAP_SECONDS,
        "rest_crosscheck_seconds": REST_CROSSCHECK_SECONDS,
    }
    await conn.execute(
        """
        INSERT INTO paper_sessions(id, mode, model_version, status, config)
        VALUES (%s, 'paper_live', %s, 'running', %s::jsonb)
        ON CONFLICT (id) DO UPDATE SET
          model_version=EXCLUDED.model_version,
          status='running',
          config=paper_sessions.config || EXCLUDED.config
        """,
        (SESSION_ID, MODEL_VERSION, json.dumps(config, separators=(",", ":"))),
    )
    await conn.commit()


async def db_writer(queue: asyncio.Queue[JournalRow | AuditRow]) -> None:
    conn = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=False)
    writer_failed = False
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
                                  raw_text, payload, payload_sha256,
                                  connection_id, prev_chain_hash, chain_hash, price_mode, source_message_type
                                ) VALUES (
                                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                                  %s::uuid,%s,%s,%s,%s
                                )
                                ON CONFLICT DO NOTHING
                                """,
                                (
                                    SESSION_ID, item.channel, item.sid, item.seq, item.market_ticker,
                                    item.exchange_ts_ms, item.received_at, item.received_epoch_ms,
                                    str(item.received_epoch_ns), str(item.received_monotonic_ns),
                                    item.raw_text, json.dumps(item.payload, separators=(",", ":")), item.payload_sha256,
                                    item.connection_id, item.prev_chain_hash, item.chain_hash,
                                    item.price_mode, item.source_message_type,
                                ),
                            )
                        else:
                            await cur.execute(
                                """
                                INSERT INTO audit_findings(
                                  session_id, severity, component, finding_code, market_ticker, details
                                ) VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                                """,
                                (
                                    SESSION_ID, item.severity, item.component, item.finding_code,
                                    item.market_ticker, json.dumps(item.details, separators=(",", ":")),
                                ),
                            )
                await conn.commit()
            except Exception:
                writer_failed = True
                await conn.rollback()
                stop_event.set()
                raise
            finally:
                for _ in batch:
                    queue.task_done()
    finally:
        try:
            await conn.execute(
                "UPDATE paper_sessions SET stopped_at=now(), status=%s WHERE id=%s",
                ("failed" if writer_failed else "stopped", SESSION_ID),
            )
            await conn.commit()
        except Exception:
            pass
        await conn.close()


async def journal_ws(
    queue: asyncio.Queue[JournalRow | AuditRow],
    raw_text: str,
    state: ConnectionJournalState,
) -> dict[str, Any]:
    wall_ns = time.time_ns()
    mono_ns = time.monotonic_ns()
    raw_bytes = raw_text.encode("utf-8")
    raw_sha = payload_hash(raw_bytes)
    chain_hash = next_chain_hash(state.prev_chain_hash, state.connection_id, wall_ns, raw_sha)
    data = json.loads(raw_text)
    msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
    exchange_ts = msg.get("ts_ms")
    ticker = msg.get("market_ticker")
    message_type = str(data.get("type", "unknown"))
    row = JournalRow(
        channel=message_type,
        sid=int(data["sid"]) if data.get("sid") is not None else None,
        seq=int(data["seq"]) if data.get("seq") is not None else None,
        market_ticker=str(ticker) if ticker is not None else None,
        exchange_ts_ms=int(exchange_ts) if exchange_ts is not None else None,
        received_at=utc_iso_from_ns(wall_ns),
        received_epoch_ms=wall_ns // 1_000_000,
        received_epoch_ns=wall_ns,
        received_monotonic_ns=mono_ns,
        raw_text=raw_text,
        payload=data,
        payload_sha256=raw_sha,
        connection_id=state.connection_id,
        prev_chain_hash=state.prev_chain_hash,
        chain_hash=chain_hash,
        price_mode=PRICE_MODE,
        source_message_type=message_type,
    )
    await queue.put(row)
    state.prev_chain_hash = chain_hash
    return data


async def journal_rest_orderbook(queue: asyncio.Queue[JournalRow | AuditRow], ticker: str) -> None:
    url = f"{REST_BASE}/markets/{urllib.parse.quote(ticker, safe='')}/orderbook?depth=0"
    before_ns = time.time_ns()
    before_mono = time.monotonic_ns()
    payload = await asyncio.to_thread(http_json, url, attempts=3)
    after_ns = time.time_ns()
    after_mono = time.monotonic_ns()
    raw_text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
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
        raw_text=raw_text,
        payload={
            "request_started_epoch_ns": str(before_ns),
            "request_rtt_ns": str(after_mono - before_mono),
            "response": payload,
        },
        payload_sha256=payload_hash(raw_text.encode("utf-8")),
        price_mode="native_outcome",
        source_message_type="rest_orderbook_crosscheck",
    ))


async def rest_crosscheck_loop(
    queue: asyncio.Queue[JournalRow | AuditRow],
    active_ref: dict[str, set[str]],
) -> None:
    while not stop_event.is_set():
        start = time.monotonic()
        markets = sorted(active_ref["markets"])
        if markets:
            # Cross-check sequentially: this path is an audit sanity check, not a
            # latency source, so REST quota is more valuable than speed here.
            for ticker in markets:
                if stop_event.is_set():
                    break
                try:
                    await journal_rest_orderbook(queue, ticker)
                except Exception as exc:
                    await queue.put(AuditRow(
                        "warning", "kalshi_rest", "REST_ORDERBOOK_CROSSCHECK_FAILED", ticker,
                        {"error": repr(exc)},
                    ))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass
        sleep_for = max(0.1, REST_CROSSCHECK_SECONDS - (time.monotonic() - start))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass


async def run_ws(
    queue: asyncio.Queue[JournalRow | AuditRow],
    active_ref: dict[str, set[str]],
) -> None:
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

        connection_id = str(uuid.uuid4())
        journal_state = ConnectionJournalState(connection_id=connection_id)
        headers = auth_headers()
        recv_task: asyncio.Task[Any] | None = None
        discovery_task: asyncio.Task[set[str]] | None = None

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
                await queue.put(AuditRow(
                    "info", "kalshi_ws", "WS_CONNECTED", None,
                    {"connection_id": connection_id, "market_count": len(markets), "price_mode": PRICE_MODE},
                ))

                await ws.send(json.dumps({
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": sorted(markets),
                        "use_yes_price": USE_YES_PRICE,
                    },
                }, separators=(",", ":")))
                await ws.send(json.dumps({
                    "id": 2,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["trade"],
                        "market_tickers": sorted(markets),
                    },
                }, separators=(",", ":")))

                last_seq: dict[int, int] = {}
                snapshot_markets: set[str] = set()
                recv_task = asyncio.create_task(ws.recv(), name=f"ws-recv-{connection_id}")
                discovery_task = asyncio.create_task(discover_after_delay(), name=f"market-discovery-{connection_id}")

                while not stop_event.is_set():
                    done, _ = await asyncio.wait(
                        {recv_task, discovery_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if recv_task in done:
                        raw = recv_task.result()
                        recv_task = asyncio.create_task(ws.recv(), name=f"ws-recv-{connection_id}")
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        data = await journal_ws(queue, raw, journal_state)
                        msg_type = data.get("type")
                        msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}

                        if msg_type == "error":
                            await queue.put(AuditRow(
                                "error", "kalshi_ws", "WS_ERROR_MESSAGE", None,
                                {"connection_id": connection_id, "message": data},
                            ))
                        elif msg_type in ("orderbook_snapshot", "orderbook_delta"):
                            sid = int(data["sid"]) if data.get("sid") is not None else None
                            seq = int(data["seq"]) if data.get("seq") is not None else None
                            ticker = str(msg.get("market_ticker") or "")
                            if sid is None or seq is None:
                                await queue.put(AuditRow(
                                    "fatal", "kalshi_ws", "ORDERBOOK_MISSING_SEQUENCE", ticker or None,
                                    {"connection_id": connection_id, "sid": sid, "seq": seq},
                                ))
                                raise SequenceGap("orderbook message missing sid/seq")

                            prev = last_seq.get(sid)
                            if prev is not None and seq != prev + 1:
                                await queue.put(AuditRow(
                                    "fatal", "kalshi_ws", "ORDERBOOK_SEQUENCE_GAP", ticker or None,
                                    {
                                        "connection_id": connection_id,
                                        "sid": sid,
                                        "expected": prev + 1,
                                        "received": seq,
                                        "missing_from": prev + 1,
                                        "missing_to": seq - 1,
                                    },
                                ))
                                raise SequenceGap(f"sid={sid} expected={prev + 1} got={seq}")
                            last_seq[sid] = seq

                            if msg_type == "orderbook_snapshot":
                                if ticker:
                                    snapshot_markets.add(ticker)
                            elif ticker not in snapshot_markets:
                                await queue.put(AuditRow(
                                    "fatal", "kalshi_ws", "DELTA_BEFORE_SNAPSHOT", ticker or None,
                                    {"connection_id": connection_id, "sid": sid, "seq": seq},
                                ))
                                raise MissingSnapshot(f"delta before snapshot for {ticker}")

                    if discovery_task in done:
                        discovered = discovery_task.result()
                        if stop_event.is_set():
                            break
                        active_ref["markets"] = set(discovered)
                        if discovered and discovered != markets:
                            await queue.put(AuditRow(
                                "info", "kalshi_ws", "MARKET_UNIVERSE_CHANGED", None,
                                {
                                    "connection_id": connection_id,
                                    "added": sorted(discovered - markets),
                                    "removed": sorted(markets - discovered),
                                },
                            ))
                            raise MarketUniverseChanged("open market set changed")
                        discovery_task = asyncio.create_task(
                            discover_after_delay(),
                            name=f"market-discovery-{connection_id}",
                        )

        except asyncio.CancelledError:
            raise
        except MarketUniverseChanged:
            reconnect_backoff = 0.5
        except (SequenceGap, MissingSnapshot) as exc:
            await queue.put(AuditRow(
                "fatal", "kalshi_ws", "WS_CONTINUITY_INVALIDATED", None,
                {"connection_id": connection_id, "error": repr(exc)},
            ))
            reconnect_backoff = 0.5
        except Exception as exc:
            await queue.put(AuditRow(
                "error", "kalshi_ws", "WS_CONNECTION_RESET", None,
                {"connection_id": connection_id, "error": repr(exc), "backoff_seconds": reconnect_backoff},
            ))
        finally:
            for task in (recv_task, discovery_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (recv_task, discovery_task) if task is not None),
                return_exceptions=True,
            )

        if not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=reconnect_backoff)
            except asyncio.TimeoutError:
                pass
            reconnect_backoff = min(30.0, max(0.5, reconnect_backoff * 2))


async def main() -> None:
    print(json.dumps({
        "event": "paper_collector_start",
        "session_id": SESSION_ID,
        "series": SERIES,
        "price_mode": PRICE_MODE,
        "key_fingerprint": KEY_FINGERPRINT,
    }))
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

    try:
        done, _ = await asyncio.wait({writer, ws_task, rest_task}, return_when=asyncio.FIRST_EXCEPTION)
        failures = [
            (task.get_name(), task.exception())
            for task in done
            if not task.cancelled() and task.exception()
        ]
        if failures:
            stop_event.set()
            for name, exc in failures:
                print(json.dumps({"event": "paper_collector_fatal", "task": name, "error": repr(exc)}))
            raise RuntimeError("; ".join(f"{name}: {exc!r}" for name, exc in failures))
    finally:
        stop_event.set()
        for task in (ws_task, rest_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(ws_task, rest_task, return_exceptions=True)

        if not writer.done():
            try:
                await asyncio.wait_for(queue.join(), timeout=15)
            except asyncio.TimeoutError:
                print(json.dumps({"event": "paper_collector_fatal", "task": "db-writer", "error": "journal drain timeout"}))
                writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)

        print(json.dumps({"event": "paper_collector_stop", "session_id": SESSION_ID}))


if __name__ == "__main__":
    asyncio.run(main())
