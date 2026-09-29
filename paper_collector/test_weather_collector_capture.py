from __future__ import annotations

import os
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://example.invalid/mercury_test")
os.environ.setdefault("PAPER_SESSION_ID", "test-awc-capture")

import weather_collector as wc

UTC = timezone.utc


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self.calls.append((text, params))
        if text.startswith("INSERT INTO live_weather_journal"):
            return Result((101,))
        raise AssertionError(f"unexpected SQL: {text}")

    def commit(self):
        self.commits += 1


class AwcCaptureTests(unittest.TestCase):
    def test_collect_journals_exact_response_before_parsed_weather_rows(self):
        raw_bytes = b'[{"icaoId":"KPHL","obsTime":1787079240,"rawOb":"KPHL 181854Z 31/20","temp":31.1}]'
        reports = [{
            "icaoId": "KPHL",
            "obsTime": 1787079240,
            "rawOb": "KPHL 181854Z 31/20",
            "temp": 31.1,
            "metarType": "METAR",
            "receiptTime": "2026-08-18T18:54:05Z",
        }]
        conn = FakeConnection()

        with patch.object(wc, "http_json_capture", return_value=(
            reports,
            raw_bytes,
            {"status": 200, "content_type": "application/json", "content_encoding": None, "date": None},
        )), patch.object(wc, "insert_raw_capture", return_value=42) as insert_raw, \
             patch.object(wc.time, "time_ns", side_effect=[1_787_079_245_000_000_000, 1_787_079_246_000_000_000]), \
             patch.object(wc.time, "monotonic_ns", side_effect=[100, 200]):
            written = wc.collect_once(conn)

        self.assertEqual(written, 1)
        self.assertEqual(conn.commits, 1)
        capture = insert_raw.call_args.args[1]
        self.assertEqual(capture.raw_bytes, raw_bytes)
        self.assertEqual(capture.source, "NOAA_AWC")
        self.assertEqual(capture.source_stream, "metar_json_batch")
        self.assertEqual(capture.received_epoch_ns, 1_787_079_246_000_000_000)
        self.assertEqual(capture.metadata["request_rtt_ns"], "100")

        self.assertEqual(len(conn.calls), 1)
        sql, params = conn.calls[0]
        self.assertIn("raw_source_id", sql)
        self.assertEqual(params[-1], 42)

    def test_empty_204_body_is_still_a_raw_capture(self):
        conn = FakeConnection()
        with patch.object(wc, "http_json_capture", return_value=(
            [], b"", {"status": 204, "content_type": None, "content_encoding": None, "date": None},
        )), patch.object(wc, "insert_raw_capture", return_value=9) as insert_raw, \
             patch.object(wc.time, "time_ns", side_effect=[10, 20]), \
             patch.object(wc.time, "monotonic_ns", side_effect=[100, 120]):
            written = wc.collect_once(conn)

        self.assertEqual(written, 0)
        self.assertEqual(insert_raw.call_args.args[1].raw_bytes, b"")
        self.assertEqual(insert_raw.call_args.args[1].metadata["http"]["status"], 204)
        self.assertEqual(conn.calls, [])
        self.assertEqual(conn.commits, 1)


if __name__ == "__main__":
    unittest.main()
