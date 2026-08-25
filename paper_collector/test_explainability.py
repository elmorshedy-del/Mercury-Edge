from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from explainability import canonical_explanation_bytes, explain_order, inspect_raw_source
from hard_information_domain import BucketElimination, HardClimateState
from raw_journal import sha256_hex

UTC = timezone.utc
DAY = date(2026, 8, 18)
KNOWN = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
OBS = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)


class Result:
    def __init__(self, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


class ExplainConnection:
    def __init__(self, *, missing_links=False, raw_hash_override=None):
        self.missing_links = missing_links
        self.raw_hash_override = raw_hash_override
        self.calls = []
        self.raw_a = b"KPHL 181854Z T0311 10311"
        self.raw_b = b"KPHL 181754Z T0306"
        self.state = HardClimateState(
            state_id="state:88",
            station_code="KPHL",
            climate_date=DAY,
            proven_daily_high_min_f=88,
            first_known_at=KNOWN,
            transition_evidence_id="ev:t0311",
            supporting_evidence_ids=("ev:t0311", "ev:6h"),
            state_model_version="hard-state-accumulator-v1",
            calendar_version="lst-climate-calendar-v1",
        )
        self.elimination = BucketElimination(
            elimination_id="elim:86-87",
            event_ticker="KXHIGHPHIL-26AUG18",
            market_ticker="KXHIGHPHIL-26AUG18-B86.5",
            station_code="KPHL",
            climate_date=DAY,
            hard_state_id=self.state.state_id,
            hard_lower_bound_f=88,
            strike_rule="floor_strike=86;cap_strike=87",
            eliminated=True,
            elimination_model_version="bucket-elimination-v1",
            reason="hard_lower_bound_above_market_cap",
        )

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self.calls.append((text, params))
        if "FROM paper_orders o JOIN paper_signals" in text:
            audit = {
                "hard_climate_state": self.state.to_dict(),
                "bucket_elimination": self.elimination.to_dict(),
                "elimination_context": {
                    "dead_market_tickers": [
                        "KXHIGHPHIL-26AUG18-B84.5",
                        "KXHIGHPHIL-26AUG18-B86.5",
                    ]
                },
            }
            return Result(one=(
                77, "s", 33, self.elimination.market_ticker, "no",
                100, 901, "filled", Decimal("0.72"), Decimal("10"),
                Decimal("7.20"), Decimal("0.03"),
                datetime(2026, 8, 18, 18, 55, 0, 100000, tzinfo=UTC),
                "canonical-dead-no-paper-v1",
                {"connection_id": "c", "snapshot_id": 4}, audit,
                self.elimination.event_ticker, "KPHL", KNOWN, "DBN", audit,
            ))
        if "FROM hard_state_transitions" in text:
            return Result(one=(
                self.state.state_id, "KPHL", DAY, 88, KNOWN,
                "ev:t0311", ["ev:t0311", "ev:6h"],
                self.state.state_model_version, self.state.calendar_version,
                "a" * 64,
            ))
        if "FROM evidence_derivations" in text:
            evidence_id = params[0]
            if evidence_id == "ev:t0311":
                return Result(one=(
                    evidence_id, "KPHL", DAY, "asos_t_group_current",
                    "benchmark_eligible", "canonical", 88, 88, [88], "T0311",
                    OBS, None, None, KNOWN, KNOWN,
                    "asos-metar-evidence-v1", "raw-asos-lattice-v1",
                    self.state.calendar_version, {"grade": "H1_CURRENT"}, "b" * 64,
                ))
            if evidence_id == "ev:6h":
                return Result(one=(
                    evidence_id, "KPHL", DAY, "asos_six_hour_max",
                    "benchmark_eligible", "canonical", 88, 88, [88], "10311",
                    OBS, None, None, KNOWN, KNOWN,
                    "asos-metar-evidence-v1", "raw-asos-lattice-v1",
                    self.state.calendar_version, {"grade": "H2_SIX_HOUR_MAX"}, "c" * 64,
                ))
        if "FROM evidence_source_links l JOIN raw_source_journal r" in text:
            if self.missing_links:
                return Result(all_rows=[])
            evidence_id = params[0]
            if evidence_id == "ev:t0311":
                return Result(all_rows=[(
                    0, 11, "NOAA_AWC", "metar_json", "KPHL",
                    OBS, None, None, KNOWN, sha256_hex(self.raw_a),
                    "application/json", None,
                )])
            if evidence_id == "ev:6h":
                return Result(all_rows=[(
                    0, 12, "NOAA_AWC", "metar_json", "KPHL",
                    OBS, None, None, KNOWN, sha256_hex(self.raw_b),
                    "application/json", None,
                )])
        if "FROM settlement_audit_results" in text:
            return Result(all_rows=[(
                "audit:1", "info", "pass", "IMPOSSIBLE_BUCKET_SETTLED_NO",
                None, None, "exchange:1", "settlement-auditor-v1",
                {"market_result": "no"}, "d" * 64,
                datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
            )])
        if "FROM raw_source_journal WHERE id=" in text:
            raw_id = int(params[0])
            raw = self.raw_a if raw_id == 11 else self.raw_b
            digest = self.raw_hash_override or sha256_hex(raw)
            return Result(one=(
                raw_id, f"raw:{raw_id}", "s", "NOAA_AWC", "metar_json", "KPHL",
                OBS, None, None, KNOWN, "https_poll", None,
                "text/plain", None, raw, digest, {"status": 200},
            ))
        raise AssertionError(f"unexpected SQL: {text}")


class ExplainabilityTests(unittest.TestCase):
    def test_order_trace_contains_state_elimination_all_supporting_evidence_and_raw_hashes(self):
        conn = ExplainConnection()
        trace = explain_order(conn, order_id=77)
        self.assertEqual(trace["order"]["event_ticker"], "KXHIGHPHIL-26AUG18")
        self.assertEqual(trace["hard_state"]["proven_daily_high_min_f"], 88)
        self.assertEqual(trace["elimination"]["elimination_id"], "elim:86-87")
        self.assertEqual(
            trace["newly_dead_market_tickers"],
            ["KXHIGHPHIL-26AUG18-B84.5", "KXHIGHPHIL-26AUG18-B86.5"],
        )
        self.assertEqual([e["evidence_id"] for e in trace["evidence"]], ["ev:t0311", "ev:6h"])
        self.assertEqual([r["raw_source_id"] for r in trace["raw_sources"]], [11, 12])
        self.assertEqual(trace["evidence"][0]["raw_identifier"], "T0311")
        self.assertEqual(trace["evidence"][0]["canonical_interpretation"]["possible_canonical_f"], [88])
        self.assertEqual(trace["evidence"][0]["clocks"]["observed_at"], OBS)
        self.assertEqual(trace["evidence"][0]["clocks"]["mercury_received_at"], KNOWN)
        self.assertEqual(trace["settlement_audits"][0]["finding_code"], "IMPOSSIBLE_BUCKET_SETTLED_NO")
        self.assertRegex(trace["trace_sha256"], r"^[0-9a-f]{64}$")

    def test_same_db_facts_have_identical_canonical_explanation_bytes(self):
        first = explain_order(ExplainConnection(), order_id=77)
        second = explain_order(ExplainConnection(), order_id=77)
        self.assertEqual(canonical_explanation_bytes(first), canonical_explanation_bytes(second))

    def test_missing_raw_links_fail_closed_instead_of_partial_trace(self):
        with self.assertRaisesRegex(ValueError, "no immutable raw-source links"):
            explain_order(ExplainConnection(missing_links=True), order_id=77)

    def test_raw_inspection_returns_exact_bytes_and_verified_hash(self):
        conn = ExplainConnection()
        raw = inspect_raw_source(conn, raw_source_id=11)
        self.assertEqual(raw["payload_sha256"], sha256_hex(conn.raw_a))
        self.assertEqual(raw["utf8_text"].encode("utf-8"), conn.raw_a)
        import base64
        self.assertEqual(base64.b64decode(raw["raw_bytes_base64"]), conn.raw_a)

    def test_raw_hash_mismatch_is_integrity_failure(self):
        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            inspect_raw_source(ExplainConnection(raw_hash_override="0" * 64), raw_source_id=11)


if __name__ == "__main__":
    unittest.main()
