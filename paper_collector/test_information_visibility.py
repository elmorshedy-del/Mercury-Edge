from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from hard_information_domain import (
    EvidenceTrust,
    EvidenceType,
    IntegrityStatus,
    SettlementEvidence,
    SourceClocks,
)
from information_visibility import (
    AvailabilityBasis,
    PublicDisclosure,
    VisibilityClass,
    build_information_view,
    build_public_information_state,
    classify_visibility,
    disclosure_time,
    disclosures_for_evidence,
    first_ordinary_public_time_proving_bound,
    to_disclosure,
)

UTC = timezone.utc
DAY = date(2026, 8, 18)


def dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 8, 18, hour, minute, second, tzinfo=UTC)


def evidence(
    evidence_id: str,
    evidence_type: EvidenceType,
    *,
    observed_at: datetime,
    received_at: datetime,
    proven_min_f: int | None,
    proven_max_f: int | None = None,
    possible: tuple[int, ...] = (),
    source: str = "NOAA_AWC",
    report_type: str | None = "METAR",
    raw_payload_hash: str | None = None,
    trust: EvidenceTrust = EvidenceTrust.BENCHMARK_ELIGIBLE,
    integrity: IntegrityStatus = IntegrityStatus.CANONICAL,
    first_fetchable_at: datetime | None = None,
    source_published_at: datetime | None = None,
    interpreted_at: datetime | None = None,
) -> SettlementEvidence:
    metadata = {"source": source}
    if report_type is not None:
        metadata["report_type"] = report_type
    if raw_payload_hash is not None:
        metadata["raw_payload_hash"] = raw_payload_hash
    return SettlementEvidence(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        station_code="KLAX",
        climate_date=DAY,
        source_record_ids=(f"raw:{evidence_id}",),
        proven_min_f=proven_min_f,
        proven_max_f=proven_max_f if proven_max_f is not None else proven_min_f,
        integrity_status=integrity,
        trust=trust,
        clocks=SourceClocks(
            observed_at=observed_at,
            source_published_at=source_published_at,
            first_fetchable_at=first_fetchable_at,
            mercury_received_at=received_at,
            mercury_interpreted_at=interpreted_at,
        ),
        parser_version="test-parser-v1",
        evidence_model_version="test-evidence-v1",
        calendar_version="lst-v1",
        raw_identifier=evidence_id,
        possible_canonical_f=possible,
        metadata=metadata,
    )


class InformationVisibilityTests(unittest.TestCase):
    def test_supported_awc_asos_products_are_ordinary_public(self) -> None:
        for evidence_type in (
            EvidenceType.ASOS_MAIN_CURRENT,
            EvidenceType.ASOS_T_GROUP_CURRENT,
            EvidenceType.ASOS_SIX_HOUR_MAX,
            EvidenceType.ASOS_24H_MAX,
        ):
            item = evidence(
                evidence_type.value,
                evidence_type,
                observed_at=dt(18),
                received_at=dt(18, 0, 5),
                proven_min_f=77,
            )
            self.assertEqual(classify_visibility(item), VisibilityClass.ORDINARY_PUBLIC)

    def test_unknown_asos_provenance_fails_closed_not_crowd_visible(self) -> None:
        item = evidence(
            "unknown-source",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18),
            received_at=dt(18, 0, 5),
            proven_min_f=77,
            source="UNVERIFIED_FEED",
        )
        self.assertEqual(classify_visibility(item), VisibilityClass.UNKNOWN)

    def test_madis_is_specialized_and_validation_products_are_validation_only(self) -> None:
        madis = evidence(
            "madis",
            EvidenceType.MADIS_RECONSTRUCTED_5M,
            observed_at=dt(17),
            received_at=dt(17, 0, 2),
            proven_min_f=77,
            source="MADIS_OMO",
            trust=EvidenceTrust.RESEARCH_ONLY,
        )
        cli = evidence(
            "cli",
            EvidenceType.CLI_MAX,
            observed_at=dt(23),
            received_at=dt(23, 5),
            proven_min_f=77,
            source="NWS_CLI",
            trust=EvidenceTrust.VALIDATION_ONLY,
        )
        self.assertEqual(classify_visibility(madis), VisibilityClass.SPECIALIZED_PUBLIC)
        self.assertEqual(classify_visibility(cli), VisibilityClass.VALIDATION_ONLY)

    def test_public_availability_never_uses_physical_observation_time(self) -> None:
        item = evidence(
            "public",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18, 0, 0),
            received_at=dt(18, 0, 9),
            proven_min_f=77,
        )
        available_at, basis = disclosure_time(item)
        self.assertEqual(available_at, dt(18, 0, 9))
        self.assertEqual(basis, AvailabilityBasis.MERCURY_PUBLIC_FETCH)
        self.assertNotEqual(available_at, item.clocks.observed_at)

    def test_first_fetchable_and_true_publication_clocks_take_precedence(self) -> None:
        fetchable = evidence(
            "fetchable",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18),
            source_published_at=dt(18, 0, 3),
            first_fetchable_at=dt(18, 0, 4),
            received_at=dt(18, 0, 8),
            proven_min_f=77,
        )
        published = evidence(
            "published",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18),
            source_published_at=dt(18, 0, 3),
            received_at=dt(18, 0, 8),
            proven_min_f=77,
        )
        self.assertEqual(disclosure_time(fetchable), (dt(18, 0, 4), AvailabilityBasis.FIRST_FETCHABLE))
        self.assertEqual(disclosure_time(published), (dt(18, 0, 3), AvailabilityBasis.SOURCE_PUBLISHED))

    def test_public_disclosure_preserves_source_product_versions_and_raw_reference(self) -> None:
        item = evidence(
            "precise-public",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18),
            received_at=dt(18, 0, 7),
            proven_min_f=77,
            possible=(77,),
            report_type="SPECI",
            raw_payload_hash="a" * 64,
        )
        disclosure = to_disclosure(item)
        self.assertIsInstance(disclosure, PublicDisclosure)
        self.assertEqual(disclosure.source, "NOAA_AWC")
        self.assertEqual(disclosure.product, "SPECI")
        self.assertEqual(disclosure.source_record_ids, ("raw:precise-public",))
        self.assertEqual(disclosure.source_payload_hash, "a" * 64)
        self.assertEqual(disclosure.parser_version, "test-parser-v1")
        self.assertEqual(disclosure.evidence_model_version, "test-evidence-v1")
        self.assertEqual(disclosure.calendar_version, "lst-v1")
        self.assertEqual(disclosure.to_dict(), to_disclosure(item).to_dict())

    def test_disclosure_stream_is_deterministic_and_causal(self) -> None:
        later = evidence(
            "later",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(19),
            received_at=dt(19, 0, 5),
            proven_min_f=76,
        )
        earlier = evidence(
            "earlier",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18),
            received_at=dt(18, 0, 5),
            proven_min_f=75,
        )
        stream = disclosures_for_evidence([later, earlier], station_code="KLAX", climate_date=DAY)
        self.assertEqual(tuple(item.evidence_id for item in stream), ("earlier", "later"))
        causal = disclosures_for_evidence(
            [later, earlier],
            station_code="KLAX",
            climate_date=DAY,
            as_of=dt(18, 30),
        )
        self.assertEqual(tuple(item.evidence_id for item in causal), ("earlier",))

    def test_hidden_max_window_closes_only_when_ordinary_public_evidence_catches_up(self) -> None:
        public_current = evidence(
            "public-current-75",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(17, 0),
            received_at=dt(17, 0, 5),
            proven_min_f=75,
            possible=(75,),
        )
        hidden = evidence(
            "madis-hidden-77",
            EvidenceType.MADIS_RECONSTRUCTED_5M,
            observed_at=dt(17, 12),
            received_at=dt(17, 12, 2),
            interpreted_at=dt(17, 12, 3),
            proven_min_f=77,
            possible=(77,),
            source="MADIS_OMO",
            trust=EvidenceTrust.RESEARCH_ONLY,
        )
        public_six = evidence(
            "public-six-hour-77",
            EvidenceType.ASOS_SIX_HOUR_MAX,
            observed_at=dt(19, 53),
            received_at=dt(19, 53, 6),
            proven_min_f=77,
            possible=(77,),
        )
        stream = [public_current, hidden, public_six]

        before_release = build_information_view(
            stream,
            station_code="KLAX",
            climate_date=DAY,
            as_of=dt(19, 30),
        )
        self.assertEqual(before_release.ordinary_public.public_daily_high_min_f, 75)
        self.assertEqual(before_release.mercury_research_high_min_f, 77)
        self.assertEqual(before_release.specialized_public_high_min_f, 77)
        self.assertEqual(before_release.research_vs_public_gap_f, 2)

        catch_up = first_ordinary_public_time_proving_bound(
            stream,
            station_code="KLAX",
            climate_date=DAY,
            target_bound_f=77,
        )
        self.assertEqual(catch_up, dt(19, 53, 6))

        after_release = build_information_view(
            stream,
            station_code="KLAX",
            climate_date=DAY,
            as_of=dt(20, 0),
        )
        self.assertEqual(after_release.ordinary_public.public_daily_high_min_f, 77)
        self.assertEqual(after_release.research_vs_public_gap_f, 0)
        self.assertIsNotNone(after_release.ordinary_public.latest_six_hour_disclosure_id)

    def test_later_lower_temperature_cannot_reduce_public_daily_high_bound(self) -> None:
        high = evidence(
            "high",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18),
            received_at=dt(18, 0, 5),
            proven_min_f=77,
            possible=(77,),
        )
        lower = evidence(
            "lower",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(19),
            received_at=dt(19, 0, 5),
            proven_min_f=74,
            possible=(74,),
        )
        state = build_public_information_state(
            [high, lower],
            station_code="KLAX",
            climate_date=DAY,
            as_of=dt(20),
        )
        self.assertEqual(state.public_daily_high_min_f, 77)
        self.assertEqual(state.latest_current_proven_min_f, 74)
        self.assertEqual(state.latest_current_observed_at, dt(19))
        self.assertEqual(state.supporting_evidence_ids, ("high",))
        self.assertEqual(len(state.supporting_disclosure_ids), 1)

    def test_late_old_report_does_not_replace_newer_public_current_observation(self) -> None:
        newer = evidence(
            "newer",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(19),
            received_at=dt(19, 0, 5),
            proven_min_f=76,
            possible=(76,),
        )
        delayed_old = evidence(
            "delayed-old",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18, 30),
            received_at=dt(19, 5),
            proven_min_f=77,
            possible=(77,),
        )
        state = build_public_information_state(
            [newer, delayed_old],
            station_code="KLAX",
            climate_date=DAY,
            as_of=dt(19, 10),
        )
        self.assertEqual(state.latest_current_proven_min_f, 76)
        self.assertEqual(state.latest_current_observed_at, dt(19))
        # The delayed older observation can still prove a higher historical max.
        self.assertEqual(state.public_daily_high_min_f, 77)

    def test_main_and_precise_current_facts_remain_separately_inspectable(self) -> None:
        main = evidence(
            "main",
            EvidenceType.ASOS_MAIN_CURRENT,
            observed_at=dt(18),
            received_at=dt(18, 0, 5),
            proven_min_f=75,
            proven_max_f=76,
            possible=(75, 76),
            integrity=IntegrityStatus.AMBIGUOUS,
        )
        precise = evidence(
            "precise",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18),
            received_at=dt(18, 0, 5),
            proven_min_f=76,
            possible=(76,),
        )
        state = build_public_information_state(
            [main, precise],
            station_code="KLAX",
            climate_date=DAY,
            as_of=dt(18, 1),
        )
        self.assertEqual(state.latest_main_current_possible_f, (75, 76))
        self.assertEqual(state.latest_precise_current_possible_f, (76,))
        self.assertIsNotNone(state.latest_main_current_disclosure_id)
        self.assertIsNotNone(state.latest_precise_current_disclosure_id)
        self.assertNotEqual(
            state.latest_main_current_disclosure_id,
            state.latest_precise_current_disclosure_id,
        )
        self.assertEqual(state.latest_current_evidence_type, EvidenceType.ASOS_T_GROUP_CURRENT)
        self.assertEqual(state.latest_current_possible_f, (76,))

    def test_validation_truth_cannot_contaminate_public_intraday_state(self) -> None:
        public = evidence(
            "public-75",
            EvidenceType.ASOS_T_GROUP_CURRENT,
            observed_at=dt(18),
            received_at=dt(18, 0, 5),
            proven_min_f=75,
            possible=(75,),
        )
        settlement = evidence(
            "settlement-80",
            EvidenceType.KALSHI_SETTLEMENT,
            observed_at=dt(23),
            received_at=dt(23, 30),
            proven_min_f=80,
            source="KALSHI",
            trust=EvidenceTrust.VALIDATION_ONLY,
        )
        state = build_public_information_state(
            [public, settlement],
            station_code="KLAX",
            climate_date=DAY,
            as_of=dt(23, 59),
        )
        self.assertEqual(state.public_daily_high_min_f, 75)

    def test_state_is_deterministic_independent_of_input_order(self) -> None:
        items = [
            evidence(
                "a",
                EvidenceType.ASOS_MAIN_CURRENT,
                observed_at=dt(18),
                received_at=dt(18, 0, 5),
                proven_min_f=75,
                proven_max_f=76,
                possible=(75, 76),
                integrity=IntegrityStatus.AMBIGUOUS,
            ),
            evidence(
                "b",
                EvidenceType.ASOS_SIX_HOUR_MAX,
                observed_at=dt(19, 53),
                received_at=dt(19, 53, 6),
                proven_min_f=77,
                possible=(77,),
            ),
        ]
        forward = build_public_information_state(
            items,
            station_code="KLAX",
            climate_date=DAY,
            as_of=dt(20),
        )
        reverse = build_public_information_state(
            list(reversed(items)),
            station_code="KLAX",
            climate_date=DAY,
            as_of=dt(20),
        )
        self.assertEqual(forward, reverse)
        self.assertEqual(forward.to_dict(), reverse.to_dict())


if __name__ == "__main__":
    unittest.main()
