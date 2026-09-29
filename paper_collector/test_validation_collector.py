from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

from stations import NWS_VALIDATION_LOCATIONS, WEATHER_STATIONS
from validation_collector import (
    HttpEntity,
    capture_kalshi_settled_event_once,
    collect_nws_validation_once,
)

UTC = timezone.utc


class Result:
    def __init__(self, *, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


class FakeConnection:
    def __init__(self, prior_validation_id=None):
        self.calls = []
        self.prior_validation_id = prior_validation_id

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self.calls.append((text, params))
        if text.startswith("SELECT source_product_id FROM validation_products"):
            return Result(all_rows=[])
        if text.startswith("SELECT validation_id FROM validation_products"):
            return Result(one=((self.prior_validation_id,) if self.prior_validation_id else None))
        raise AssertionError(f"unexpected SQL: {text}")


class CaptureRecorder:
    def __init__(self):
        self.captures = []
        self.products = []

    def insert_raw(self, conn, capture):
        self.captures.append(capture)
        return 100 + len(self.captures)

    def persist_product(self, conn, *, session_id, product, raw_source_id):
        self.products.append((session_id, product, raw_source_id, len(self.captures)))
        return product.validation_id


def entity(url: str, body: bytes, *, second: int, status: int = 200) -> HttpEntity:
    started = datetime(2026, 8, 22, 5, 30, second, tzinfo=UTC)
    received = datetime(2026, 8, 22, 5, 30, second + 1, tzinfo=UTC)
    return HttpEntity(
        url=url,
        status=status,
        body=body,
        headers={"Content-Type": "application/geo+json"},
        request_started_at=started,
        request_started_monotonic_ns=second * 1_000_000_000,
        received_at=received,
        received_epoch_ns=int(received.timestamp() * 1_000_000_000),
        received_monotonic_ns=second * 1_000_000_000 + 50_000_000,
    )


def nws_bodies(kind="CLI", *, detail_id="prod-1", detail_code=None, text=None):
    issued = "2026-08-22T05:00:00+00:00"
    index = json.dumps({
        "@graph": [{"id": "prod-1", "issuanceTime": issued, "productCode": kind}],
    }).encode()
    if text is None:
        text = (
            "CLIMATE SUMMARY FOR AUGUST 21 2026\nMAXIMUM 77\n"
            if kind == "CLI"
            else "KNYC DS 21/08 771425/ 650510// 77/ 65//"
        )
    detail = json.dumps({
        "id": detail_id,
        "issuanceTime": issued,
        "productCode": detail_code or kind,
        "productText": text,
    }).encode()
    return index, detail


def fetcher_for(index_body: bytes, detail_body: bytes, *, status=200):
    def fetch(url: str):
        if "/products/types/" in url:
            return entity(url, index_body, second=0, status=status)
        return entity(url, detail_body, second=2, status=status)
    return fetch


class StationCoverageTests(unittest.TestCase):
    def test_every_python_weather_station_has_explicit_nws_product_location(self) -> None:
        self.assertEqual(set(WEATHER_STATIONS), set(NWS_VALIDATION_LOCATIONS))


class NwsRawFirstCollectionTests(unittest.TestCase):
    def test_invalid_index_json_is_journaled_before_parse_failure(self) -> None:
        recorder = CaptureRecorder()
        result = collect_nws_validation_once(
            FakeConnection(),
            session_id="s",
            station_code="KNYC",
            product_type="CLI",
            fetcher=lambda url: entity(url, b"not-json", second=0),
            insert_raw=recorder.insert_raw,
            persist_product=recorder.persist_product,
        )
        self.assertEqual(len(recorder.captures), 1)
        self.assertEqual(recorder.captures[0].raw_bytes, b"not-json")
        self.assertEqual(result.raw_source_ids, (101,))
        self.assertTrue(result.issues[0].startswith("index_parse:"))
        self.assertEqual(recorder.products, [])

    def test_cli_detail_bytes_are_journaled_before_validation_persistence(self) -> None:
        index, detail = nws_bodies("CLI")
        recorder = CaptureRecorder()
        conn = FakeConnection()
        result = collect_nws_validation_once(
            conn,
            session_id="s",
            station_code="KNYC",
            product_type="CLI",
            fetcher=fetcher_for(index, detail),
            insert_raw=recorder.insert_raw,
            persist_product=recorder.persist_product,
        )
        self.assertEqual(len(recorder.captures), 2)
        self.assertEqual(recorder.captures[0].source_stream, "product_index:CLI:NYC")
        self.assertEqual(recorder.captures[1].source_stream, "product_detail:CLI")
        self.assertEqual(recorder.captures[1].raw_bytes, detail)
        self.assertEqual(len(recorder.products), 1)
        session_id, product, raw_source_id, capture_count_at_persist = recorder.products[0]
        self.assertEqual(session_id, "s")
        self.assertEqual(raw_source_id, 102)
        self.assertEqual(capture_count_at_persist, 2)
        self.assertEqual(product.source_record_id, "raw_source_journal:102")
        self.assertEqual(product.source_payload_sha256, recorder.captures[1].payload_sha256)
        self.assertEqual(product.issued_at, datetime(2026, 8, 22, 5, 0, tzinfo=UTC))
        self.assertEqual(product.mercury_received_at, datetime(2026, 8, 22, 5, 30, 3, tzinfo=UTC))
        self.assertEqual(result.validation_ids, (product.validation_id,))
        sql = "\n".join(call[0] for call in conn.calls)
        self.assertNotIn("product_releases", sql)

    def test_detail_identity_mismatch_preserves_raw_bytes_but_creates_no_product(self) -> None:
        index, detail = nws_bodies("CLI", detail_id="different")
        recorder = CaptureRecorder()
        result = collect_nws_validation_once(
            FakeConnection(),
            session_id="s",
            station_code="KNYC",
            product_type="CLI",
            fetcher=fetcher_for(index, detail),
            insert_raw=recorder.insert_raw,
            persist_product=recorder.persist_product,
        )
        self.assertEqual(len(recorder.captures), 2)
        self.assertEqual(recorder.captures[-1].raw_bytes, detail)
        self.assertEqual(recorder.products, [])
        self.assertIn("detail_product_id_mismatch", result.issues[0])

    def test_corrected_dsm_links_to_prior_immutable_validation_version(self) -> None:
        index, detail = nws_bodies(
            "DSM",
            text="KNYC DS COR 21/08 781425/ 650510// 78/ 65//",
        )
        recorder = CaptureRecorder()
        result = collect_nws_validation_once(
            FakeConnection(prior_validation_id="validation:prior"),
            session_id="s",
            station_code="KNYC",
            product_type="DSM",
            fetcher=fetcher_for(index, detail),
            insert_raw=recorder.insert_raw,
            persist_product=recorder.persist_product,
        )
        self.assertEqual(len(result.validation_ids), 1)
        product = recorder.products[0][1]
        self.assertTrue(product.corrected)
        self.assertEqual(product.revision_of, "validation:prior")

    def test_non_success_detail_response_is_still_raw_journaled(self) -> None:
        index, detail = nws_bodies("CLI")
        def fetch(url: str):
            if "/products/types/" in url:
                return entity(url, index, second=0)
            return entity(url, detail, second=2, status=503)
        recorder = CaptureRecorder()
        result = collect_nws_validation_once(
            FakeConnection(),
            session_id="s",
            station_code="KNYC",
            product_type="CLI",
            fetcher=fetch,
            insert_raw=recorder.insert_raw,
            persist_product=recorder.persist_product,
        )
        self.assertEqual(len(recorder.captures), 2)
        self.assertEqual(recorder.captures[-1].metadata["http_status"], 503)
        self.assertEqual(recorder.products, [])
        self.assertIn("detail_http_status:prod-1:503", result.issues)


class KalshiSettlementCaptureTests(unittest.TestCase):
    def test_settled_event_is_journaled_before_market_result_interpretation(self) -> None:
        body = json.dumps({
            "event": {
                "event_ticker": "KXHIGHNY-26AUG21",
                "markets": [
                    {"ticker": "M1", "result": "no"},
                    {"ticker": "M2", "result": "yes"},
                ],
            }
        }).encode()
        recorder = CaptureRecorder()
        captured = capture_kalshi_settled_event_once(
            FakeConnection(),
            session_id="s",
            event_ticker="KXHIGHNY-26AUG21",
            station_code="KNYC",
            fetcher=lambda url: entity(url, body, second=4),
            insert_raw=recorder.insert_raw,
        )
        self.assertEqual(len(recorder.captures), 1)
        self.assertEqual(recorder.captures[0].raw_bytes, body)
        self.assertEqual(recorder.captures[0].source_stream, "settled_event_detail")
        self.assertTrue(captured.fully_resolved)
        self.assertEqual(captured.market_results, (("M1", "no"), ("M2", "yes")))

    def test_invalid_settled_event_json_is_preserved_and_fails_closed(self) -> None:
        recorder = CaptureRecorder()
        captured = capture_kalshi_settled_event_once(
            FakeConnection(),
            session_id="s",
            event_ticker="KXHIGHNY-26AUG21",
            station_code="KNYC",
            fetcher=lambda url: entity(url, b"{bad", second=4),
            insert_raw=recorder.insert_raw,
        )
        self.assertEqual(len(recorder.captures), 1)
        self.assertFalse(captured.fully_resolved)
        self.assertEqual(captured.fail_closed_reason, "invalid_json_entity")

    def test_unresolved_market_does_not_claim_fully_settled_event(self) -> None:
        body = json.dumps({
            "event": {
                "event_ticker": "KXHIGHNY-26AUG21",
                "markets": [{"ticker": "M1", "result": ""}],
            }
        }).encode()
        captured = capture_kalshi_settled_event_once(
            FakeConnection(),
            session_id="s",
            event_ticker="KXHIGHNY-26AUG21",
            station_code="KNYC",
            fetcher=lambda url: entity(url, body, second=4),
            insert_raw=CaptureRecorder().insert_raw,
        )
        self.assertFalse(captured.fully_resolved)
        self.assertEqual(captured.market_results, (("M1", "unknown"),))
        self.assertEqual(captured.fail_closed_reason, "one_or_more_markets_unresolved")


if __name__ == "__main__":
    unittest.main()
