from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from settlement_audit_domain import build_exchange_market_settlement
from settlement_journal import persist_exchange_market_settlement

UTC = timezone.utc
DAY = date(2026, 8, 21)
RAW_HASH = "a" * 64
RULES_HASH = "b" * 64


class Result:
    def __init__(self, one=None):
        self.one = one

    def fetchone(self):
        return self.one


class Connection:
    def __init__(self):
        self.calls = []
        self.hashes = {}
        self.raw_row = ("s", "KNYC", RAW_HASH)
        self.force_hash = None

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self.calls.append((text, params))
        if text.startswith("SELECT session_id,station_code,payload_sha256 FROM raw_source_journal"):
            return Result(self.raw_row)
        if text.startswith("INSERT INTO exchange_market_settlements"):
            self.hashes.setdefault(str(params[0]), str(params[-1]))
            return Result()
        if text.startswith("SELECT settlement_sha256 FROM exchange_market_settlements"):
            value = self.force_hash or self.hashes.get(str(params[0]))
            return Result((value,) if value else None)
        raise AssertionError(f"unexpected SQL: {text}")


def settlement():
    return build_exchange_market_settlement(
        event_ticker="KXHIGHNY-26AUG21",
        station_code="KNYC",
        climate_date=DAY,
        source_record_id="raw_source_journal:900",
        source_payload_sha256=RAW_HASH,
        rules_hash=RULES_HASH,
        rule_source_name="The Weather Company",
        captured_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        market_results=(("M1", "no"), ("M2", "yes")),
    )


class ExchangeSettlementJournalTests(unittest.TestCase):
    def test_exchange_settlement_is_insert_only_idempotent_and_raw_linked(self) -> None:
        conn = Connection()
        item = settlement()
        first = persist_exchange_market_settlement(conn, session_id="s", settlement=item, raw_source_id=900)
        second = persist_exchange_market_settlement(conn, session_id="s", settlement=item, raw_source_id=900)
        self.assertEqual(first, item.exchange_settlement_id)
        self.assertEqual(second, item.exchange_settlement_id)
        sql = "\n".join(call[0] for call in conn.calls)
        self.assertIn("INSERT INTO exchange_market_settlements", sql)
        self.assertNotIn("UPDATE exchange_market_settlements", sql)
        self.assertNotIn("DELETE FROM exchange_market_settlements", sql)

    def test_exchange_settlement_raw_hash_mismatch_fails_closed(self) -> None:
        conn = Connection()
        conn.raw_row = ("s", "KNYC", "0" * 64)
        with self.assertRaisesRegex(ValueError, "payload hash"):
            persist_exchange_market_settlement(conn, session_id="s", settlement=settlement(), raw_source_id=900)

    def test_exchange_settlement_same_identity_different_bytes_fails_closed(self) -> None:
        conn = Connection()
        item = settlement()
        persist_exchange_market_settlement(conn, session_id="s", settlement=item, raw_source_id=900)
        conn.force_hash = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "collision"):
            persist_exchange_market_settlement(conn, session_id="s", settlement=item, raw_source_id=900)


if __name__ == "__main__":
    unittest.main()
