from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from madis_omo import ContractMadisOmoAdapter, MadisMinuteStatus
from madis_transport import (
    MADIS_ARCHIVE_STREAM,
    MADIS_LIVE_STREAM,
    MADIS_TRANSPORT_MODEL_VERSION,
    CapturedMadisRecord,
    MadisDataOrigin,
    MadisTransportEnvelope,
    SourceTransportEvent,
    TransportEventType,
    parse_captured_madis_omo,
    persist_madis_transport_record,
)
from raw_journal import raw_source_record

UTC = timezone.utc
NOW = datetime(2026, 8, 20, 17, 0, tzinfo=UTC)


def envelope(
    *,
    origin: MadisDataOrigin = MadisDataOrigin.LIVE_LDM,
    raw_bytes: bytes = b"\x00OMO\xff",
    received: datetime = NOW,
    epoch_ns: int = 1_755_709_200_000_000_000,
    first_fetchable: datetime | None = None,
) -> MadisTransportEnvelope:
    return MadisTransportEnvelope(
        session_id="session-1",
        origin=origin,
        raw_bytes=raw_bytes,
        received_at=received,
        received_epoch_ns=epoch_ns,
        received_monotonic_ns=123_456_789,
        product_id="MADIS-OMO-KLAX-20260820T1700",
        connection_id="ldm-connection-7" if origin is MadisDataOrigin.LIVE_LDM else None,
        sequence_key="seq:100",
        reconnect_generation=2 if origin is MadisDataOrigin.LIVE_LDM else None,
        station_code="KLAX",
        observed_at=NOW - timedelta(seconds=5),
        source_published_at=NOW - timedelta(seconds=2),
        first_fetchable_at=first_fetchable,
        content_type="application/octet-stream",
        metadata={"feed_type": "EXP"},
    )


class MadisTransportEnvelopeTests(unittest.TestCase):
    def test_live_ldm_capture_preserves_exact_bytes_and_all_receipt_identity(self) -> None:
        env = envelope(first_fetchable=NOW - timedelta(seconds=1))
        capture = env.to_raw_capture()
        self.assertEqual(capture.raw_bytes, b"\x00OMO\xff")
        self.assertEqual(capture.source_stream, MADIS_LIVE_STREAM)
        self.assertEqual(capture.transport, "ldm")
        self.assertEqual(capture.received_at, NOW)
        self.assertEqual(capture.received_epoch_ns, env.received_epoch_ns)
        self.assertEqual(capture.received_monotonic_ns, env.received_monotonic_ns)
        self.assertEqual(capture.first_fetchable_at, NOW - timedelta(seconds=1))
        self.assertTrue(capture.metadata["live_causal"])
        self.assertEqual(capture.metadata["product_id"], env.product_id)
        self.assertEqual(capture.metadata["connection_id"], env.connection_id)
        self.assertEqual(capture.metadata["transport_model_version"], MADIS_TRANSPORT_MODEL_VERSION)

    def test_archive_import_is_structurally_distinct_and_cannot_claim_live_fetchability(self) -> None:
        claimed = NOW - timedelta(hours=2)
        env = envelope(origin=MadisDataOrigin.ARCHIVE_IMPORT, first_fetchable=claimed)
        capture = env.to_raw_capture()
        self.assertEqual(capture.source_stream, MADIS_ARCHIVE_STREAM)
        self.assertEqual(capture.transport, "archive_import")
        self.assertFalse(capture.metadata["live_causal"])
        self.assertIsNone(capture.first_fetchable_at)
        self.assertEqual(capture.metadata["archive_claimed_first_fetchable_at"], claimed.isoformat())

    def test_identical_bytes_received_later_are_distinct_causal_captures(self) -> None:
        first = envelope()
        later = envelope(received=NOW + timedelta(seconds=1), epoch_ns=1_755_709_201_000_000_000)
        self.assertEqual(first.raw_bytes, later.raw_bytes)
        self.assertNotEqual(first.to_raw_capture().capture_id, later.to_raw_capture().capture_id)

    def test_non_bytes_payload_is_rejected_before_journaling(self) -> None:
        with self.assertRaises(TypeError):
            MadisTransportEnvelope(  # type: ignore[arg-type]
                session_id="s",
                origin=MadisDataOrigin.LIVE_LDM,
                raw_bytes="not-bytes",
                received_at=NOW,
                received_epoch_ns=1,
                received_monotonic_ns=1,
                product_id="p",
            )


class PersistThenParseContractTests(unittest.TestCase):
    def test_persistence_happens_before_parser_facing_record_exists(self) -> None:
        env = envelope()
        with patch("madis_transport.insert_raw_capture", return_value=42) as insert:
            captured = persist_madis_transport_record(object(), env)  # type: ignore[arg-type]
        insert.assert_called_once()
        self.assertEqual(captured.raw_source_id, 42)
        self.assertEqual(captured.raw_record.record_id, "raw_source_journal:42")
        self.assertEqual(captured.raw_record.payload_hash, env.to_raw_capture().payload_sha256)
        self.assertTrue(captured.live_causal)

    def test_live_parse_uses_actual_receipt_clock_and_marks_live_causality(self) -> None:
        env = envelope()
        capture = env.to_raw_capture()
        captured = CapturedMadisRecord(
            raw_source_id=7,
            capture=capture,
            raw_record=raw_source_record(capture, 7),
            origin=env.origin,
            product_id=env.product_id,
            connection_id=env.connection_id,
            reconnect_generation=env.reconnect_generation,
        )
        interpreted = NOW + timedelta(milliseconds=250)
        result = parse_captured_madis_omo(
            captured,
            station_timezone="America/Los_Angeles",
            mercury_interpreted_at=interpreted,
            fields={
                "station_code": "KLAX",
                "observed_at": (NOW - timedelta(seconds=5)).isoformat(),
                "temperature": Decimal("304.25"),
                "temperature_unit": "K",
                "upstream_variable": "T",
                "temperature_sensor_status": 0,
            },
            adapter=ContractMadisOmoAdapter(),
        )
        self.assertEqual(result.minute.status, MadisMinuteStatus.ACCEPTED_RESEARCH)
        self.assertEqual(result.minute.ldm_received_at, NOW)
        self.assertEqual(result.minute.mercury_interpreted_at, interpreted)
        self.assertTrue(result.minute.metadata["live_causal"])
        self.assertEqual(result.minute.metadata["raw_source_id"], 7)
        self.assertEqual(result.minute.metadata["transport_connection_id"], env.connection_id)

    def test_archive_parse_remains_research_metadata_not_live_causal(self) -> None:
        env = envelope(origin=MadisDataOrigin.ARCHIVE_IMPORT)
        capture = env.to_raw_capture()
        captured = CapturedMadisRecord(
            raw_source_id=8,
            capture=capture,
            raw_record=raw_source_record(capture, 8),
            origin=env.origin,
            product_id=env.product_id,
            connection_id=None,
            reconnect_generation=None,
        )
        result = parse_captured_madis_omo(
            captured,
            station_timezone="America/Los_Angeles",
            mercury_interpreted_at=NOW + timedelta(milliseconds=100),
            fields={
                "station_code": "KLAX",
                "observed_at": (NOW - timedelta(seconds=5)).isoformat(),
                "temperature": Decimal("304.25"),
                "temperature_unit": "K",
                "upstream_variable": "T",
                "temperature_sensor_status": 0,
            },
        )
        self.assertFalse(result.minute.metadata["live_causal"])
        self.assertEqual(result.minute.metadata["madis_data_origin"], "archive_import")
        self.assertEqual(result.minute.ldm_received_at, NOW)


class TransportContinuityTests(unittest.TestCase):
    def test_sequence_gap_is_explicit_deterministic_interval(self) -> None:
        event = SourceTransportEvent(
            session_id="session-1",
            source="MADIS_OMO",
            source_stream=MADIS_LIVE_STREAM,
            event_type=TransportEventType.SEQUENCE_GAP,
            detected_at=NOW,
            detected_epoch_ns=1_755_709_200_000_000_000,
            detected_monotonic_ns=555,
            connection_id="ldm-1",
            interval_start_at=NOW - timedelta(seconds=4),
            interval_end_at=NOW,
            prior_sequence_key="seq:100",
            next_sequence_key="seq:105",
            details={"missing_count": 4},
        )
        same = SourceTransportEvent(**{
            "session_id": "session-1",
            "source": "MADIS_OMO",
            "source_stream": MADIS_LIVE_STREAM,
            "event_type": TransportEventType.SEQUENCE_GAP,
            "detected_at": NOW,
            "detected_epoch_ns": 1_755_709_200_000_000_000,
            "detected_monotonic_ns": 555,
            "connection_id": "ldm-1",
            "interval_start_at": NOW - timedelta(seconds=4),
            "interval_end_at": NOW,
            "prior_sequence_key": "seq:100",
            "next_sequence_key": "seq:105",
            "details": {"missing_count": 4},
        })
        self.assertEqual(event.event_id, same.event_id)
        self.assertEqual(event.event_sha256, same.event_sha256)
        self.assertEqual(event.event_type, TransportEventType.SEQUENCE_GAP)
        self.assertLess(event.interval_start_at, event.interval_end_at)  # type: ignore[arg-type]

    def test_invalid_gap_interval_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            SourceTransportEvent(
                session_id="session-1",
                source="MADIS_OMO",
                source_stream=MADIS_LIVE_STREAM,
                event_type=TransportEventType.QUEUE_GAP,
                detected_at=NOW,
                detected_epoch_ns=1,
                detected_monotonic_ns=1,
                interval_start_at=NOW,
                interval_end_at=NOW - timedelta(seconds=1),
            )


if __name__ == "__main__":
    unittest.main()
