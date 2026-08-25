from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from failure_events import HardEdgeFailureEvent, failure_counts, persist_failure_event

UTC = timezone.utc
NOW = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)


class Result:
    def __init__(self, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


class Conn:
    def __init__(self):
        self.hashes = {}
        self.calls = []
        self.count_rows = [
            ("execution", "economic_skip", "NO_POSITIVE_GUARANTEED_RETURN_AFTER_FEES", 2),
            ("source_parse", "integrity_failure", "ASOS_OFF_LATTICE_EVIDENCE", 1),
        ]

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self.calls.append((text, params))
        if text.startswith("SELECT session_id,station_code FROM raw_source_journal"):
            return Result(one=("s", "KPHL"))
        if text.startswith("INSERT INTO hard_edge_failure_events"):
            failure_id = str(params[0])
            self.hashes.setdefault(failure_id, str(params[-1]))
            return Result()
        if text.startswith("SELECT failure_sha256 FROM hard_edge_failure_events"):
            return Result(one=(self.hashes.get(str(params[0])),))
        if text.startswith("SELECT stage,disposition_class,reason_code,count(*)"):
            return Result(all_rows=self.count_rows)
        raise AssertionError(f"unexpected SQL: {text}")


def event(**changes):
    values = dict(
        session_id="s",
        stage="source_parse",
        disposition_class="integrity_failure",
        reason_code="ASOS_OFF_LATTICE_EVIDENCE",
        occurred_at=NOW,
        station_code="KPHL",
        climate_date=date(2026, 8, 18),
        raw_source_id=11,
        details={"raw_group": "T0310"},
    )
    values.update(changes)
    return HardEdgeFailureEvent(**values)


class FailureEventTests(unittest.TestCase):
    def test_same_failure_fact_is_idempotent(self):
        conn = Conn()
        a = event()
        b = event()
        self.assertEqual(a.failure_id, b.failure_id)
        self.assertEqual(persist_failure_event(conn, event=a), a.failure_id)
        self.assertEqual(persist_failure_event(conn, event=b), a.failure_id)

    def test_same_identity_with_changed_payload_fails_closed(self):
        conn = Conn()
        persist_failure_event(conn, event=event(details={"raw_group": "T0310"}))
        with self.assertRaisesRegex(RuntimeError, "collision"):
            persist_failure_event(conn, event=event(details={"raw_group": "T0310", "new": True}))

    def test_raw_link_is_verified_against_session_and_station(self):
        conn = Conn()
        persist_failure_event(conn, event=event())
        self.assertTrue(any("raw_source_journal" in call[0] for call in conn.calls))

    def test_reason_counts_preserve_economic_skip_vs_integrity_failure(self):
        rows = failure_counts(Conn(), session_id="s")
        self.assertEqual(rows[0]["disposition_class"], "economic_skip")
        self.assertEqual(rows[1]["disposition_class"], "integrity_failure")
        self.assertNotEqual(rows[0]["disposition_class"], rows[1]["disposition_class"])

    def test_invalid_reason_code_is_rejected(self):
        with self.assertRaises(ValueError):
            event(reason_code="not stable")


if __name__ == "__main__":
    unittest.main()
