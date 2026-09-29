from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import unittest

from hard_information_domain import (
    BucketElimination,
    EvidenceTrust,
    EvidenceType,
    ExecutableMarketState,
    HardClimateState,
    IntegrityStatus,
    NormalizedObservation,
    RawSourceRecord,
    SettlementEvidence,
    SettlementTruth,
    SourceClocks,
)

UTC = timezone.utc


def clocks() -> SourceClocks:
    return SourceClocks(
        observed_at=datetime(2026, 8, 18, 18, 53, tzinfo=UTC),
        source_published_at=datetime(2026, 8, 18, 18, 53, 5, tzinfo=UTC),
        first_fetchable_at=datetime(2026, 8, 18, 18, 53, 6, tzinfo=UTC),
        mercury_received_at=datetime(2026, 8, 18, 18, 53, 7, tzinfo=UTC),
        mercury_interpreted_at=datetime(2026, 8, 18, 18, 53, 7, 500000, tzinfo=UTC),
    )


class DomainRoundTripTests(unittest.TestCase):
    def assert_round_trip(self, obj, cls) -> None:
        encoded = obj.to_dict()
        json.dumps(encoded, sort_keys=True)  # must be plain deterministic JSON data
        restored = cls.from_dict(encoded)
        self.assertEqual(restored, obj)
        self.assertEqual(restored.to_dict(), encoded)

    def test_source_clocks_round_trip(self) -> None:
        self.assert_round_trip(clocks(), SourceClocks)

    def test_raw_source_record_round_trip(self) -> None:
        self.assert_round_trip(
            RawSourceRecord(
                record_id="weather:123",
                source="NOAA_AWC",
                station_code="KLAX",
                payload_hash="sha256:abc",
                clocks=clocks(),
                transport="https_poll",
                sequence_key="123",
            ),
            RawSourceRecord,
        )

    def test_normalized_observation_preserves_decimal_type(self) -> None:
        obj = NormalizedObservation(
            observation_id="obs:123:t",
            source_record_id="weather:123",
            station_code="KLAX",
            climate_date=date(2026, 8, 18),
            observation_type="asos_t_group_current",
            value=Decimal("25.0"),
            unit="degC",
            clocks=clocks(),
            parser_version="metar-v1",
            calendar_version="lst-v1",
            metadata={"raw_group": "T02500194"},
        )
        self.assert_round_trip(obj, NormalizedObservation)
        self.assertEqual(obj.to_dict()["value_type"], "decimal")

    def test_settlement_evidence_round_trip(self) -> None:
        self.assert_round_trip(
            SettlementEvidence(
                evidence_id="ev:123:6h",
                evidence_type=EvidenceType.ASOS_SIX_HOUR_MAX,
                station_code="KLAX",
                climate_date=date(2026, 8, 18),
                source_record_ids=("weather:123",),
                proven_min_f=77,
                proven_max_f=77,
                integrity_status=IntegrityStatus.CANONICAL,
                trust=EvidenceTrust.BENCHMARK_ELIGIBLE,
                clocks=clocks(),
                parser_version="metar-v1",
                evidence_model_version="asos-lattice-v1",
                calendar_version="lst-v1",
                raw_identifier="10250",
                possible_canonical_f=(77,),
                metadata={"grade": "H2_SIX_HOUR_MAX"},
            ),
            SettlementEvidence,
        )

    def test_hard_state_round_trip_and_bound_logic(self) -> None:
        obj = HardClimateState(
            state_id="state:KLAX:2026-08-18:77",
            station_code="KLAX",
            climate_date=date(2026, 8, 18),
            proven_daily_high_min_f=77,
            first_known_at=datetime(2026, 8, 18, 22, 53, 7, tzinfo=UTC),
            transition_evidence_id="ev:123:6h",
            supporting_evidence_ids=("ev:123:6h",),
            state_model_version="hard-state-v1",
            calendar_version="lst-v1",
        )
        self.assert_round_trip(obj, HardClimateState)
        self.assertTrue(obj.proves_above(76))
        self.assertFalse(obj.proves_above(77))

    def test_bucket_elimination_round_trip(self) -> None:
        self.assert_round_trip(
            BucketElimination(
                elimination_id="elim:E:M:state",
                event_ticker="E",
                market_ticker="M",
                station_code="KLAX",
                climate_date=date(2026, 8, 18),
                hard_state_id="state",
                hard_lower_bound_f=77,
                strike_rule="max_f<=76",
                eliminated=True,
                elimination_model_version="elim-v1",
                reason="hard lower bound 77 exceeds market cap 76",
            ),
            BucketElimination,
        )

    def test_executable_market_state_round_trip(self) -> None:
        self.assert_round_trip(
            ExecutableMarketState(
                event_ticker="E",
                market_ticker="M",
                captured_at=datetime(2026, 8, 18, 22, 53, 8, tzinfo=UTC),
                no_ask=Decimal("0.73"),
                no_ask_quantity=Decimal("50"),
                fee_estimate=Decimal("0.01"),
                market_data_version="kalshi-l2-v1",
                source_record_id="book:1",
            ),
            ExecutableMarketState,
        )

    def test_settlement_truth_round_trip(self) -> None:
        self.assert_round_trip(
            SettlementTruth(
                truth_id="truth:1",
                source="NWS_CLI",
                station_code="KLAX",
                climate_date=date(2026, 8, 18),
                final_max_f=77,
                status="final",
                source_record_id="cli:1",
                observed_or_issued_at=datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
                truth_model_version="settlement-truth-v1",
            ),
            SettlementTruth,
        )


class TrustBoundaryTests(unittest.TestCase):
    def make_evidence(self, *, trust: EvidenceTrust, integrity: IntegrityStatus, proven=88) -> SettlementEvidence:
        return SettlementEvidence(
            evidence_id="ev",
            evidence_type=EvidenceType.ASOS_T_GROUP_CURRENT,
            station_code="KPHL",
            climate_date=date(2026, 8, 18),
            source_record_ids=("raw",),
            proven_min_f=proven,
            proven_max_f=proven,
            integrity_status=integrity,
            trust=trust,
            clocks=clocks(),
            parser_version="metar-v1",
            evidence_model_version="asos-v1",
            calendar_version="lst-v1",
        )

    def test_only_explicit_trusted_integrity_can_be_benchmark_eligible(self) -> None:
        self.assertTrue(self.make_evidence(
            trust=EvidenceTrust.BENCHMARK_ELIGIBLE,
            integrity=IntegrityStatus.CANONICAL,
        ).benchmark_eligible)
        self.assertTrue(self.make_evidence(
            trust=EvidenceTrust.BENCHMARK_ELIGIBLE,
            integrity=IntegrityStatus.AMBIGUOUS,
        ).benchmark_eligible)  # lossy main-C can still prove a conservative lower bound

        for trust in (EvidenceTrust.VALIDATION_ONLY, EvidenceTrust.RESEARCH_ONLY, EvidenceTrust.REJECTED):
            self.assertFalse(self.make_evidence(trust=trust, integrity=IntegrityStatus.CANONICAL).benchmark_eligible)

        for integrity in (
            IntegrityStatus.OFF_LATTICE,
            IntegrityStatus.CONFLICT,
            IntegrityStatus.INCOMPLETE,
            IntegrityStatus.INVALID_WINDOW,
            IntegrityStatus.UNKNOWN,
        ):
            self.assertFalse(self.make_evidence(
                trust=EvidenceTrust.BENCHMARK_ELIGIBLE,
                integrity=integrity,
            ).benchmark_eligible)

    def test_missing_bound_is_not_benchmark_eligible(self) -> None:
        self.assertFalse(self.make_evidence(
            trust=EvidenceTrust.BENCHMARK_ELIGIBLE,
            integrity=IntegrityStatus.CANONICAL,
            proven=None,
        ).benchmark_eligible)

    def test_cli_type_can_be_forced_validation_only(self) -> None:
        evidence = SettlementEvidence(
            evidence_id="cli",
            evidence_type=EvidenceType.CLI_MAX,
            station_code="KLAX",
            climate_date=date(2026, 8, 18),
            source_record_ids=("cli:1",),
            proven_min_f=77,
            proven_max_f=77,
            integrity_status=IntegrityStatus.CANONICAL,
            trust=EvidenceTrust.VALIDATION_ONLY,
            clocks=clocks(),
            parser_version="cli-v1",
            evidence_model_version="validation-v1",
            calendar_version="lst-v1",
        )
        self.assertFalse(evidence.benchmark_eligible)


if __name__ == "__main__":
    unittest.main()
