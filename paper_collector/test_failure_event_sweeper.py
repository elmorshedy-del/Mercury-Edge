from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from failure_event_sweeper import (
    _asos_events,
    _elimination_events,
    _execution_events,
    _settlement_events,
    _validation_events,
)

UTC = timezone.utc
T = datetime(2026, 8, 18, 19, 0, tzinfo=UTC)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class QueryConn:
    def __init__(self, rows_by_marker):
        self.rows_by_marker = rows_by_marker

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        for marker, rows in self.rows_by_marker.items():
            if marker in text:
                return Result(rows)
        raise AssertionError(f"unexpected SQL: {text}")


class AsosDiagnosticTests(unittest.TestCase):
    def _events(self, raw_text, observed=T):
        conn = QueryConn({
            "FROM live_weather_journal": [
                (1, 11, "KPHL", observed, T, raw_text),
            ]
        })
        return list(_asos_events(conn, session_id="s"))

    def test_off_lattice_is_countable_integrity_failure(self):
        events = self._events("KPHL 181900Z 31/20 T03100000")
        self.assertEqual([e.reason_code for e in events], ["ASOS_OFF_LATTICE_EVIDENCE"])
        self.assertEqual(events[0].disposition_class, "integrity_failure")
        self.assertEqual(events[0].raw_source_id, 11)

    def test_main_t_conflict_is_explicit(self):
        events = self._events("KPHL 181900Z 29/20 T03110000")
        self.assertEqual([e.reason_code for e in events], ["ASOS_MAIN_T_CONFLICT"])

    def test_six_hour_crossing_lst_day_is_non_admission(self):
        # 06:00Z in August Philadelphia = 01:00 LST; a six-hour lookback crosses climate midnight.
        observed = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
        events = self._events("KPHL 180600Z 20/10 T02000000 10200", observed=observed)
        self.assertIn("ASOS_SIX_HOUR_CROSSES_CLIMATE_DAY", [e.reason_code for e in events])
        self.assertEqual(events[0].disposition_class, "non_admission")

    def test_24_hour_group_is_explicit_benchmark_non_admission(self):
        events = self._events("KPHL 181900Z 31/20 T03110000 403110200")
        self.assertIn("ASOS_24_HOUR_BENCHMARK_DEFERRED", [e.reason_code for e in events])


class LedgerSourceTests(unittest.TestCase):
    def test_elimination_audit_finding_becomes_fail_closed_event(self):
        conn = QueryConn({
            "FROM audit_findings": [
                (7, T, "KPHL", None, {
                    "reason": "missing_event_rules_hash",
                    "event_ticker": "KXHIGHPHIL-26AUG18",
                    "hard_state_id": "state:88",
                })
            ]
        })
        event = list(_elimination_events(conn, session_id="s"))[0]
        self.assertEqual(event.reason_code, "BUCKET_ELIMINATION_MISSING_EVENT_RULES_HASH")
        self.assertEqual(event.stage, "elimination")

    def test_execution_no_edge_is_distinct_from_fail_closed(self):
        conn = QueryConn({
            "FROM paper_portfolio_decisions": [
                (1, T, "skip", "NO_POSITIVE_GUARANTEED_RETURN_AFTER_FEES", {}, 10, "KPHL", "E", T, "balanced"),
                (2, T, "blocked", "NO_VALID_L2_AT_SIMULATED_ARRIVAL", {}, 11, "KPHL", "E", T, "balanced"),
            ]
        })
        events = list(_execution_events(conn, session_id="s"))
        self.assertEqual(events[0].disposition_class, "economic_skip")
        self.assertEqual(events[1].disposition_class, "fail_closed")

    def test_rejected_validation_is_raw_linked(self):
        conn = QueryConn({
            "FROM validation_products": [
                ("validation:1", "KPHL", date(2026, 8, 18), T, 44, "rejected",
                 "cli_explicit_report_date_missing", "NWS_CLI", "CLI-PHL"),
            ]
        })
        event = list(_validation_events(conn, session_id="s"))[0]
        self.assertEqual(event.raw_source_id, 44)
        self.assertEqual(event.reason_code, "VALIDATION_CLI_EXPLICIT_REPORT_DATE_MISSING")

    def test_settlement_invariant_failure_keeps_trade_identity(self):
        conn = QueryConn({
            "FROM settlement_audit_results": [
                ("audit:1", T, "IMPOSSIBLE_BUCKET_SETTLED_YES", "KPHL", date(2026, 8, 18),
                 "state:88", "elim:1", 77, "M", {"event_ticker": "E"}),
            ]
        })
        event = list(_settlement_events(conn, session_id="s"))[0]
        self.assertEqual(event.disposition_class, "invariant_failure")
        self.assertEqual(event.order_id, 77)
        self.assertEqual(event.elimination_id, "elim:1")


if __name__ == "__main__":
    unittest.main()
