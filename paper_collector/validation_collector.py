from __future__ import annotations

"""Raw-first Step 4H-C source collectors.

Network entity bytes are journaled before JSON/product parsing. NWS DSM/CLI are
normalized as validation-only products. Kalshi settled-event capture is kept
separate and does not guess a final temperature from market metadata.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.parse
import urllib.request

import psycopg

from raw_journal import RawCapture, insert_raw_capture
from settlement_journal import persist_validation_product
from settlement_validation import ValidationProduct, parse_nws_cli, parse_nws_dsm
from stations import NWS_VALIDATION_LOCATIONS, STATIONS

UTC = timezone.utc
NWS_BASE_URL = os.getenv("NWS_BASE_URL", "https://api.weather.gov").rstrip("/")
KALSHI_REST_BASE = os.getenv("KALSHI_REST_BASE", "https://external-api.kalshi.com/trade-api/v2").rstrip("/")
SOURCE_USER_AGENT = os.getenv("SOURCE_USER_AGENT", "MercuryEdge/1.1 research@example.com")
VALIDATION_COLLECTOR_VERSION = "validation-collector-v1"


@dataclass(frozen=True)
class HttpEntity:
    url: str
    status: int
    body: bytes
    headers: Mapping[str, str]
    request_started_at: datetime
    request_started_monotonic_ns: int
    received_at: datetime
    received_epoch_ns: int
    received_monotonic_ns: int

    @property
    def payload_sha256(self) -> str:
        return sha256(self.body).hexdigest()

    @property
    def request_rtt_ms(self) -> float:
        return max(0, self.received_monotonic_ns - self.request_started_monotonic_ns) / 1_000_000


@dataclass(frozen=True)
class ValidationCollectionResult:
    station_code: str
    product_type: str
    raw_source_ids: tuple[int, ...]
    validation_ids: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class SettledEventCapture:
    raw_source_id: int
    event_ticker: str
    station_code: str
    payload_sha256: str
    fully_resolved: bool
    market_results: tuple[tuple[str, str], ...]
    fail_closed_reason: str | None = None


HttpFetcher = Callable[[str], HttpEntity]
RawInserter = Callable[[psycopg.Connection[Any], RawCapture], int]
ProductPersister = Callable[..., str]


def fetch_http_entity(url: str) -> HttpEntity:
    start_wall = datetime.now(UTC)
    start_mono = time.monotonic_ns()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": SOURCE_USER_AGENT,
            "Accept": "application/geo+json, application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read()
            status = int(response.status)
            headers = {str(k): str(v) for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        # Even an HTTP error entity is a causal source response. Preserve its
        # exact bytes before the caller decides it is unusable.
        body = exc.read()
        status = int(exc.code)
        headers = {str(k): str(v) for k, v in exc.headers.items()}
    received_ns = time.time_ns()
    received_mono = time.monotonic_ns()
    return HttpEntity(
        url=url,
        status=status,
        body=body,
        headers=headers,
        request_started_at=start_wall,
        request_started_monotonic_ns=start_mono,
        received_at=datetime.fromtimestamp(received_ns / 1_000_000_000, tz=UTC),
        received_epoch_ns=received_ns,
        received_monotonic_ns=received_mono,
    )


def journal_http_entity(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    source: str,
    source_stream: str,
    station_code: str,
    entity: HttpEntity,
    source_published_at: datetime | None = None,
    sequence_key: str | None = None,
    insert_raw: RawInserter = insert_raw_capture,
) -> int:
    content_type = _header(entity.headers, "content-type") or "application/octet-stream"
    capture = RawCapture(
        session_id=session_id,
        source=source,
        source_stream=source_stream,
        station_code=station_code,
        raw_bytes=entity.body,
        received_at=entity.received_at,
        received_epoch_ns=entity.received_epoch_ns,
        received_monotonic_ns=entity.received_monotonic_ns,
        transport="https_poll",
        content_type=content_type,
        content_encoding=_header(entity.headers, "content-encoding"),
        source_published_at=source_published_at,
        sequence_key=sequence_key,
        metadata={
            "collector_version": VALIDATION_COLLECTOR_VERSION,
            "url": entity.url,
            "http_status": entity.status,
            "request_started_at": entity.request_started_at.isoformat(),
            "request_started_monotonic_ns": entity.request_started_monotonic_ns,
            "request_rtt_ms": entity.request_rtt_ms,
            "headers": dict(entity.headers),
        },
    )
    return insert_raw(conn, capture)


def collect_nws_validation_once(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    station_code: str,
    product_type: str,
    limit: int = 8,
    fetcher: HttpFetcher = fetch_http_entity,
    insert_raw: RawInserter = insert_raw_capture,
    persist_product: ProductPersister = persist_validation_product,
) -> ValidationCollectionResult:
    station = station_code.strip().upper()
    kind = product_type.strip().upper()
    if kind not in {"DSM", "CLI"}:
        raise ValueError("product_type must be DSM or CLI")
    if station not in STATIONS:
        raise ValueError("unknown Mercury station")
    location = NWS_VALIDATION_LOCATIONS.get(station)
    if not location:
        raise ValueError("station has no explicit NWS validation location")
    timezone_name = STATIONS[station]["timezone"]

    raw_ids: list[int] = []
    validation_ids: list[str] = []
    issues: list[str] = []

    index_url = f"{NWS_BASE_URL}/products/types/{urllib.parse.quote(kind)}/locations/{urllib.parse.quote(location)}"
    index_entity = fetcher(index_url)
    index_raw_id = journal_http_entity(
        conn,
        session_id=session_id,
        source="NWS_API",
        source_stream=f"product_index:{kind}:{location}",
        station_code=station,
        entity=index_entity,
        insert_raw=insert_raw,
    )
    raw_ids.append(index_raw_id)
    if not 200 <= index_entity.status < 300:
        issues.append(f"index_http_status:{index_entity.status}")
        return ValidationCollectionResult(station, kind, tuple(raw_ids), (), tuple(issues))

    try:
        items = _decode_nws_index(index_entity.body, expected_code=kind)
    except ValueError as exc:
        issues.append(f"index_parse:{exc}")
        return ValidationCollectionResult(station, kind, tuple(raw_ids), (), tuple(issues))

    known = _known_product_ids(conn, session_id=session_id, source=f"NWS_{kind}", station_code=station, limit=max(8, limit * 4))
    selected = [item for item in items if item[0] not in known][: max(0, int(limit))]

    for product_id, index_issued_at in selected:
        detail_url = f"{NWS_BASE_URL}/products/{urllib.parse.quote(product_id, safe='')}"
        entity = fetcher(detail_url)
        detail_raw_id = journal_http_entity(
            conn,
            session_id=session_id,
            source="NWS_API",
            source_stream=f"product_detail:{kind}",
            station_code=station,
            entity=entity,
            source_published_at=index_issued_at,
            sequence_key=product_id,
            insert_raw=insert_raw,
        )
        raw_ids.append(detail_raw_id)
        if not 200 <= entity.status < 300:
            issues.append(f"detail_http_status:{product_id}:{entity.status}")
            continue

        try:
            detail = _decode_json_object(entity.body)
            detail_id = str(detail.get("id") or "")
            code = str(detail.get("productCode") or "").upper()
            raw_text = detail.get("productText")
            issued_raw = detail.get("issuanceTime")
            if detail_id != product_id:
                raise ValueError("detail_product_id_mismatch")
            if code != kind:
                raise ValueError("detail_product_code_mismatch")
            if not isinstance(raw_text, str) or not raw_text:
                raise ValueError("detail_product_text_missing")
            issued_at = _parse_datetime(issued_raw)
            if index_issued_at is not None and issued_at != index_issued_at:
                raise ValueError("detail_issuance_time_mismatch")
        except ValueError as exc:
            issues.append(f"detail_parse:{product_id}:{exc}")
            continue

        parser = parse_nws_dsm if kind == "DSM" else parse_nws_cli
        product: ValidationProduct = parser(
            raw_text,
            source_product_id=product_id,
            station_code=station,
            timezone_name=timezone_name,
            issued_at=issued_at,
            mercury_received_at=entity.received_at,
            source_record_id=f"raw_source_journal:{detail_raw_id}",
            source_payload_sha256=entity.payload_sha256,
        )
        if product.corrected and product.climate_date is not None:
            prior = _prior_validation_id(
                conn,
                session_id=session_id,
                source=product.source,
                station_code=station,
                climate_day=product.climate_date,
                before_issued_at=product.issued_at,
            )
            if prior:
                product = replace(product, revision_of=prior)

        validation_id = persist_product(
            conn,
            session_id=session_id,
            product=product,
            raw_source_id=detail_raw_id,
        )
        validation_ids.append(validation_id)

    return ValidationCollectionResult(
        station_code=station,
        product_type=kind,
        raw_source_ids=tuple(raw_ids),
        validation_ids=tuple(validation_ids),
        issues=tuple(issues),
    )


def capture_kalshi_settled_event_once(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    event_ticker: str,
    station_code: str,
    fetcher: HttpFetcher = fetch_http_entity,
    insert_raw: RawInserter = insert_raw_capture,
) -> SettledEventCapture:
    url = (
        f"{KALSHI_REST_BASE}/events/"
        f"{urllib.parse.quote(event_ticker, safe='')}?with_nested_markets=true"
    )
    entity = fetcher(url)
    raw_id = journal_http_entity(
        conn,
        session_id=session_id,
        source="KALSHI_REST",
        source_stream="settled_event_detail",
        station_code=station_code.strip().upper(),
        entity=entity,
        sequence_key=event_ticker,
        insert_raw=insert_raw,
    )
    if not 200 <= entity.status < 300:
        return SettledEventCapture(
            raw_source_id=raw_id,
            event_ticker=event_ticker,
            station_code=station_code.strip().upper(),
            payload_sha256=entity.payload_sha256,
            fully_resolved=False,
            market_results=(),
            fail_closed_reason=f"event_http_status:{entity.status}",
        )
    try:
        payload = _decode_json_object(entity.body)
        event = payload.get("event")
        if not isinstance(event, dict):
            raise ValueError("event_object_missing")
        if str(event.get("event_ticker") or "") != event_ticker:
            raise ValueError("event_ticker_mismatch")
        markets = event.get("markets")
        if not isinstance(markets, list) or not markets:
            raise ValueError("nested_markets_missing")
        results: list[tuple[str, str]] = []
        fully_resolved = True
        for market in markets:
            if not isinstance(market, dict) or not market.get("ticker"):
                raise ValueError("market_identity_missing")
            result = str(market.get("result") or "").lower()
            if result not in {"yes", "no"}:
                fully_resolved = False
                result = "unknown"
            results.append((str(market["ticker"]), result))
    except ValueError as exc:
        return SettledEventCapture(
            raw_source_id=raw_id,
            event_ticker=event_ticker,
            station_code=station_code.strip().upper(),
            payload_sha256=entity.payload_sha256,
            fully_resolved=False,
            market_results=(),
            fail_closed_reason=str(exc),
        )
    return SettledEventCapture(
        raw_source_id=raw_id,
        event_ticker=event_ticker,
        station_code=station_code.strip().upper(),
        payload_sha256=entity.payload_sha256,
        fully_resolved=fully_resolved,
        market_results=tuple(sorted(results)),
        fail_closed_reason=None if fully_resolved else "one_or_more_markets_unresolved",
    )


def _decode_nws_index(raw_bytes: bytes, *, expected_code: str) -> tuple[tuple[str, datetime | None], ...]:
    payload = _decode_json_object(raw_bytes)
    graph = payload.get("@graph")
    if not isinstance(graph, list):
        raise ValueError("product_graph_missing")
    items: list[tuple[str, datetime | None]] = []
    for item in graph:
        if not isinstance(item, dict):
            continue
        product_id = str(item.get("id") or "")
        code = str(item.get("productCode") or "").upper()
        if not product_id or code != expected_code:
            continue
        issued_raw = item.get("issuanceTime")
        issued = _parse_datetime(issued_raw) if issued_raw else None
        items.append((product_id, issued))
    return tuple(items)


def _decode_json_object(raw_bytes: bytes) -> dict[str, Any]:
    if not isinstance(raw_bytes, bytes):
        raise ValueError("entity_is_not_bytes")
    try:
        value = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_json_entity") from exc
    if not isinstance(value, dict):
        raise ValueError("json_entity_not_object")
    return value


def _known_product_ids(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    source: str,
    station_code: str,
    limit: int,
) -> set[str]:
    rows = conn.execute(
        """
        SELECT source_product_id
        FROM validation_products
        WHERE session_id=%s AND source=%s AND station_code=%s
        ORDER BY issued_at DESC, validation_id DESC
        LIMIT %s
        """,
        (session_id, source, station_code, int(limit)),
    ).fetchall()
    return {str(row[0]) for row in rows}


def _prior_validation_id(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    source: str,
    station_code: str,
    climate_day: Any,
    before_issued_at: datetime,
) -> str | None:
    row = conn.execute(
        """
        SELECT validation_id
        FROM validation_products
        WHERE session_id=%s AND source=%s AND station_code=%s
          AND climate_date=%s AND issued_at < %s
        ORDER BY issued_at DESC, validation_id DESC
        LIMIT 1
        """,
        (session_id, source, station_code, climate_day, before_issued_at),
    ).fetchone()
    return str(row[0]) if row else None


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("issuance_time_missing")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("issuance_time_invalid") from exc
    if result.tzinfo is None:
        raise ValueError("issuance_time_not_timezone_aware")
    return result


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == target:
            return str(value)
    return None
