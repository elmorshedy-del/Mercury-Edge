from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from settlement_journal import (
    SettlementAuditResult,
    persist_authoritative_settlement,
    persist_settlement_audit_result,
    persist_validation_product,
)
from settlement_validation import build_authoritative_settlement, parse_nws_cli

UTC = timezone.utc
DAY = date(2026, 8, 21)
RAW_HASH = "a" * 64


class Result:
    def __init__(self, one=None):
        self.one = one

    def fetchone(self):
        return self.one


class JournalConnection:
    def __init__(self):
        self.calls = []
        self.raw_rows = {
            101: ("s", "KNYC", RAW_HASH),
            202: ("s", "KNYC", "b" * 64),
        }
        self.validation_hashes = {}
        self.settlement_hashes = {}
        self.audit_hashes = {}
        self.force_validation_hash = None

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self.calls.append((text, params))
        if text.startswith("SELECT session_id,station_code,payload_sha256 FROM raw_source_journal"):
            return Result(self.raw_rows.get(int(params[0])))
        if text.startswith("INSERT INTO validation_products"):
            key = str(params[0])
            self.validation_hashes.setdefault(key, str(params[-1]))
            return Result()
        if text.startswith("SELECT product_sha256 FROM validation_products"):
            key = str(params[0])
            value = self.force_validation_hash or self.validation_hashes.get(key)
            return Result((value,) if value else None)
        if text.startswith("INSERT INTO authoritative_settlements"):
            key = str(params[0])
            self.settlement_hashes.setdefault(key, str(params[-1]))
            return Result()
        if text.startswith("SELECT settlement_sha256 FROM authoritative_settlements"):
            value = self.settlement_hashes.get(str(params[0]))
            return Result((value,) if value else None)
        if text.startswith("INSERT INTO settlement_audit_results"):
            key = str(params[0])
            self.audit_hashes.setdefault(key, str(params[-1]))
            return Result()
        if text.startswith("SELECT audit_sha256 FROM settlement_audit_results"):
            value = self.audit_hashes.get(str(params[0]))
            return Result((value,) if value else None)
        raise AssertionError(f"unexpected SQL: {text}")


def validation_product():
    issued = datetime(2026, 8, 22, 5, 30, tzinfo=UTC)
    return parse_nws_cli(
        "CLIMATE SUMMARY FOR AUGUST 21 2026\nMAXIMUM 77\n",
        source_product_id="cli:1",
        station_code="KNYC",
        timezone_name="America/New_York",
        issued_at=issued,
        mercury_received_at=issued,
        source_record_id="raw_source_journal:101",
        source_payload_sha256=RAW_HASH,
    )


def settlement():
    return build_authoritative_settlement(
        event_ticker="KXHIGHNY-26AUG21",
        station_code="KNYC",
        climate_day=DAY,
        final_max_f=77,
        source_record_id="raw_source_journal:202",
        observed_or_issued_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        rules_hash="c" * 64,
        rule_source_name="The Weather Company",
        settlement_source_name="The Weather Company",
    )


class ValidationPersistenceTests(unittest.TestCase):
    def test_validation_product_is_insert_only_idempotent_and_raw_linked(self) -> None:
        conn = JournalConnection()
        product = validation_product()
        first = persist_validation_product(conn, session_id="s", product=product, raw_source_id=101)
        second = persist_validation_product(conn, session_id="s", product=product, raw_source_id=101)
        self.assertEqual(first, product.validation_id)
        self.assertEqual(second, product.validation_id)
        sql = "\n".join(call[0] for call in conn.calls)
        self.assertIn("INSERT INTO validation_products", sql)
        self.assertNotIn("UPDATE validation_products", sql)
        self.assertNotIn("DELETE FROM validation_products", sql)

    def test_validation_raw_record_identity_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "source_record_id"):
            persist_validation_product(
                JournalConnection(),
                session_id="s",
                product=validation_product(),
                raw_source_id=202,
            )

    def test_validation_raw_hash_mismatch_fails_closed(self) -> None:
        conn = JournalConnection()
        conn.raw_rows[101] = ("s", "KNYC", "0" * 64)
        with self.assertRaisesRegex(ValueError, "payload hash"):
            persist_validation_product(conn, session_id="s", product=validation_product(), raw_source_id=101)

    def test_validation_same_identity_different_bytes_fails_closed(self) -> None:
        conn = JournalConnection()
        product = validation_product()
        persist_validation_product(conn, session_id="s", product=product, raw_source_id=101)
        conn.force_validation_hash = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "collision"):
            persist_validation_product(conn, session_id="s", product=product, raw_source_id=101)


class SettlementPersistenceTests(unittest.TestCase):
    def test_authoritative_settlement_is_insert_only_and_raw_linked(self) -> None:
        conn = JournalConnection()
        item = settlement()
        self.assertEqual(
            persist_authoritative_settlement(conn, session_id="s", settlement=item, raw_source_id=202),
            item.settlement_id,
        )
        sql = "\n".join(call[0] for call in conn.calls)
        self.assertNotIn("UPDATE authoritative_settlements", sql)
        self.assertNotIn("DELETE FROM authoritative_settlements", sql)

    def test_authoritative_settlement_wrong_session_fails_closed(self) -> None:
        conn = JournalConnection()
        with self.assertRaisesRegex(ValueError, "different session"):
            persist_authoritative_settlement(conn, session_id="other", settlement=settlement(), raw_source_id=202)


class AuditPersistenceTests(unittest.TestCase):
    def test_audit_identity_is_deterministic_and_insert_only(self) -> None:
        result = SettlementAuditResult(
            session_id="s",
            settlement_id="settlement:1",
            severity="critical",
            status="invariant_failure",
            finding_code="HARD_STATE_EXCEEDS_FINAL_MAX",
            station_code="KNYC",
            climate_date=DAY,
            state_id="state:1",
            details={"hard_lower_bound_f": 78, "final_max_f": 77},
        )
        self.assertEqual(result.audit_id, result.audit_id)
        conn = JournalConnection()
        first = persist_settlement_audit_result(conn, result=result)
        second = persist_settlement_audit_result(conn, result=result)
        self.assertEqual(first, second)
        sql = "\n".join(call[0] for call in conn.calls)
        self.assertNotIn("UPDATE settlement_audit_results", sql)
        self.assertNotIn("DELETE FROM settlement_audit_results", sql)

    def test_audit_requires_truth_or_validation_source(self) -> None:
        with self.assertRaises(ValueError):
            SettlementAuditResult(
                session_id="s",
                severity="info",
                status="pass",
                finding_code="NO_SOURCE",
                station_code="KNYC",
                climate_date=DAY,
            )


if __name__ == "__main__":
    unittest.main()
