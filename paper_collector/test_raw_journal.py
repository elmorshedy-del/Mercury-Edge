from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from hard_information_domain import (
    EvidenceTrust,
    EvidenceType,
    IntegrityStatus,
    SettlementEvidence,
    SourceClocks,
)
from raw_journal import RawCapture, insert_raw_capture, persist_evidence_derivation, sha256_hex

UTC = timezone.utc


class Result:
    def __init__(self, *, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


class CaptureConnection:
    def __init__(self, insert_row=(17,)):
        self.insert_row = insert_row
        self.calls = []

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self.calls.append((text, params))
        if text.startswith("INSERT INTO raw_source_journal"):
            return Result(one=self.insert_row)
        if text.startswith("SELECT id,payload_sha256 FROM raw_source_journal"):
            return Result(one=(17, self.expected_hash))
        raise AssertionError(f"unexpected SQL: {text}")


class EvidenceConnection:
    def __init__(self):
        self.calls = []
        self.derivation_hash = None
        self.links = []

    def execute(self, query, params=None):
        text = " ".join(str(query).split())
        self.calls.append((text, params))
        if text.startswith("INSERT INTO evidence_derivations"):
            self.derivation_hash = str(params[-1])
            return Result()
        if text.startswith("SELECT derivation_sha256 FROM evidence_derivations"):
            return Result(one=(self.derivation_hash,))
        if text.startswith("INSERT INTO evidence_source_links"):
            candidate = (int(params[2]), int(params[1]))
            if candidate not in self.links:
                self.links.append(candidate)
            return Result()
        if text.startswith("SELECT raw_source_id FROM evidence_source_links"):
            return Result(all_rows=[(raw_id,) for _, raw_id in sorted(self.links)])
        raise AssertionError(f"unexpected SQL: {text}")


def capture(*, raw=b'{"a":1}', received_ns=1_777_000_000_000_000_000):
    received = datetime.fromtimestamp(received_ns / 1_000_000_000, tz=UTC)
    return RawCapture(
        session_id="s",
        source="NOAA_AWC",
        source_stream="metar_json",
        raw_bytes=raw,
        received_at=received,
        received_epoch_ns=received_ns,
        received_monotonic_ns=999,
        transport="https_poll",
        content_type="application/json",
        metadata={"status": 200},
    )


def evidence(evidence_id="ev:v1", *, model_version="proof-v1"):
    observed = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
    received = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
    return SettlementEvidence(
        evidence_id=evidence_id,
        evidence_type=EvidenceType.ASOS_T_GROUP_CURRENT,
        station_code="KPHL",
        climate_date=date(2026, 8, 18),
        source_record_ids=("raw_source_journal:1",),
        proven_min_f=88,
        proven_max_f=88,
        integrity_status=IntegrityStatus.CANONICAL,
        trust=EvidenceTrust.BENCHMARK_ELIGIBLE,
        clocks=SourceClocks(observed_at=observed, mercury_received_at=received),
        parser_version="metar-v1",
        evidence_model_version=model_version,
        calendar_version="lst-v1",
        raw_identifier="T0311",
        possible_canonical_f=(88,),
        metadata={"encoded_c": "31.1"},
    )


class RawHashTests(unittest.TestCase):
    def test_hash_is_over_exact_bytes_not_semantic_json(self):
        self.assertNotEqual(sha256_hex(b'{"a":1}'), sha256_hex(b'{ "a": 1 }'))
        self.assertNotEqual(sha256_hex(b'{"a":1,"b":2}'), sha256_hex(b'{"b":2,"a":1}'))

    def test_capture_identity_is_idempotent_for_same_receipt(self):
        a = capture(raw=b"abc", received_ns=100)
        b = capture(raw=b"abc", received_ns=100)
        self.assertEqual(a.capture_id, b.capture_id)
        self.assertEqual(a.payload_sha256, b.payload_sha256)

    def test_same_payload_received_later_is_a_distinct_causal_capture(self):
        self.assertNotEqual(
            capture(raw=b"abc", received_ns=100).capture_id,
            capture(raw=b"abc", received_ns=101).capture_id,
        )

    def test_payload_must_be_bytes(self):
        with self.assertRaises(TypeError):
            sha256_hex("abc")  # type: ignore[arg-type]


class RawPersistenceTests(unittest.TestCase):
    def test_capture_write_is_insert_only(self):
        conn = CaptureConnection(insert_row=(17,))
        row_id = insert_raw_capture(conn, capture())
        self.assertEqual(row_id, 17)
        sql = "\n".join(call[0] for call in conn.calls)
        self.assertIn("INSERT INTO raw_source_journal", sql)
        self.assertNotIn("UPDATE raw_source_journal", sql)
        self.assertNotIn("DELETE FROM raw_source_journal", sql)

    def test_idempotent_retry_reads_existing_without_mutation(self):
        cap = capture(raw=b"same")
        conn = CaptureConnection(insert_row=None)
        conn.expected_hash = cap.payload_sha256
        self.assertEqual(insert_raw_capture(conn, cap), 17)
        sql = "\n".join(call[0] for call in conn.calls)
        self.assertNotIn("UPDATE raw_source_journal", sql)

    def test_identity_collision_fails_closed(self):
        cap = capture(raw=b"same")
        conn = CaptureConnection(insert_row=None)
        conn.expected_hash = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "identity collision"):
            insert_raw_capture(conn, cap)


class EvidencePersistenceTests(unittest.TestCase):
    def test_derivation_is_append_only_and_links_all_raw_inputs_in_order(self):
        conn = EvidenceConnection()
        result = persist_evidence_derivation(
            conn,
            session_id="s",
            evidence=evidence(),
            raw_source_ids=[11, 12, 11],
        )
        self.assertEqual(result, "ev:v1")
        self.assertEqual(conn.links, [(0, 11), (1, 12)])
        sql = "\n".join(call[0] for call in conn.calls)
        self.assertNotIn("UPDATE evidence_derivations", sql)
        self.assertNotIn("DELETE FROM evidence_derivations", sql)

    def test_derivation_requires_raw_provenance(self):
        with self.assertRaises(ValueError):
            persist_evidence_derivation(
                EvidenceConnection(),
                session_id="s",
                evidence=evidence(),
                raw_source_ids=[],
            )

    def test_same_logical_fact_can_coexist_when_versioned_evidence_id_changes(self):
        first = evidence("ev:model-v1", model_version="proof-v1")
        second = evidence("ev:model-v2", model_version="proof-v2")
        self.assertNotEqual(first.evidence_id, second.evidence_id)
        self.assertNotEqual(first.to_dict()["evidence_model_version"], second.to_dict()["evidence_model_version"])


if __name__ == "__main__":
    unittest.main()
