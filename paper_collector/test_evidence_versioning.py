from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

import hard_state_proof as hp

UTC = timezone.utc


class EvidenceIdentityTests(unittest.TestCase):
    def record(self) -> hp.ProofRecord:
        at = datetime(2026, 8, 18, 18, 55, tzinfo=UTC)
        return hp.ProofRecord(
            weather_id=7,
            raw_source_id=42,
            source="NOAA_AWC",
            report_type="METAR",
            observed_at=at,
            first_seen_at=at,
            received_epoch_ms=int(at.timestamp() * 1000),
            kind="t_group",
            raw_group="T03110200",
            encoded_c=Decimal("31.1"),
            possible_canonical_f=(88,),
            proven_min_f=88,
            proven_max_f=88,
            grade=hp.H1_CURRENT,
        )

    def test_same_input_same_versions_same_evidence_id(self):
        record = self.record()
        first = record.evidence_id("KPHL", date(2026, 8, 18))
        second = record.evidence_id("KPHL", date(2026, 8, 18))
        self.assertEqual(first, second)

    def test_parser_or_evidence_version_change_changes_evidence_id(self):
        record = self.record()
        original = record.evidence_id("KPHL", date(2026, 8, 18))
        with patch.object(hp, "PROOF_VERSION", "raw-asos-lattice-v2"):
            evidence_v2 = record.evidence_id("KPHL", date(2026, 8, 18))
        with patch.object(hp, "PARSER_VERSION", "asos-metar-evidence-v2"):
            parser_v2 = record.evidence_id("KPHL", date(2026, 8, 18))
        self.assertNotEqual(original, evidence_v2)
        self.assertNotEqual(original, parser_v2)
        self.assertNotEqual(evidence_v2, parser_v2)

    def test_raw_capture_change_changes_evidence_id(self):
        first = self.record()
        second = hp.ProofRecord(**{**first.__dict__, "raw_source_id": 43})
        self.assertNotEqual(
            first.evidence_id("KPHL", date(2026, 8, 18)),
            second.evidence_id("KPHL", date(2026, 8, 18)),
        )


if __name__ == "__main__":
    unittest.main()
