from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from hard_information_domain import EvidenceTrust
from hard_state_accumulator import accumulate_hard_state
from settlement_validation import (
    AuthoritativeSettlement,
    ValidationAuthority,
    ValidationLifecycle,
    ValidationProduct,
    build_authoritative_settlement,
    parse_nws_cli,
    parse_nws_dsm,
)

UTC = timezone.utc
DAY = date(2026, 8, 21)
TZ = "America/New_York"


def full_dsm(*, issued: datetime, text: str | None = None):
    return parse_nws_dsm(
        text or "KNYC DS 21/08 771425/ 650510// 77/ 65//",
        source_product_id="dsm:1",
        station_code="KNYC",
        timezone_name=TZ,
        issued_at=issued,
        mercury_received_at=issued,
        source_record_id="raw_source_journal:101",
        source_payload_sha256="a" * 64,
    )


def cli(*, issued: datetime, report_day: int = 21, text: str | None = None):
    return parse_nws_cli(
        text or (
            "CLIMATE REPORT\n"
            f"THE CENTRAL PARK NY CLIMATE SUMMARY FOR AUGUST {report_day} 2026\n"
            "TEMPERATURE (F)\n"
            "MAXIMUM 77\n"
        ),
        source_product_id="cli:1",
        station_code="KNYC",
        timezone_name=TZ,
        issued_at=issued,
        mercury_received_at=issued,
        source_record_id="raw_source_journal:102",
        source_payload_sha256="b" * 64,
    )


class DsmLifecycleTests(unittest.TestCase):
    def test_completed_dsm_maps_exact_prior_lst_climate_date_and_max_time(self) -> None:
        # 05:30Z is 00:30 on the fixed EST climate clock.
        product = full_dsm(issued=datetime(2026, 8, 22, 5, 30, tzinfo=UTC))
        self.assertEqual(product.climate_date, DAY)
        self.assertEqual(product.reported_max_f, 77)
        self.assertEqual(product.lifecycle, ValidationLifecycle.COMPLETED_DAY_PRELIMINARY)
        self.assertEqual(product.authority, ValidationAuthority.CORROBORATION_ONLY)
        self.assertEqual(product.max_observed_at, datetime(2026, 8, 21, 19, 25, tzinfo=UTC))
        self.assertTrue(product.accepted_validation)

    def test_partial_dsm_form_is_current_day_preliminary_not_completed_truth(self) -> None:
        product = full_dsm(
            issued=datetime(2026, 8, 21, 20, 15, tzinfo=UTC),
            text="KNYC DS 1500 21/08 771425/ 650510// 77/ 65//",
        )
        self.assertEqual(product.climate_date, DAY)
        self.assertEqual(product.lifecycle, ValidationLifecycle.CURRENT_DAY_PRELIMINARY)
        self.assertEqual(product.metadata["partial_cutoff_lst"], "1500")

    def test_correction_is_preserved_without_changing_validation_authority(self) -> None:
        product = full_dsm(
            issued=datetime(2026, 8, 23, 5, 30, tzinfo=UTC),
            text="KNYC DS COR 21/08 781425/ 650510// 78/ 65//",
        )
        self.assertTrue(product.corrected)
        self.assertEqual(product.reported_max_f, 78)
        self.assertEqual(product.lifecycle, ValidationLifecycle.COMPLETED_DAY_PRELIMINARY)
        self.assertEqual(product.authority, ValidationAuthority.CORROBORATION_ONLY)

    def test_completed_form_for_same_issue_climate_date_fails_closed(self) -> None:
        product = full_dsm(issued=datetime(2026, 8, 21, 18, 0, tzinfo=UTC))
        self.assertEqual(product.lifecycle, ValidationLifecycle.AMBIGUOUS)
        self.assertFalse(product.accepted_validation)
        self.assertEqual(product.fail_closed_reason, "dsm_completed_form_not_for_prior_climate_date")

    def test_unparseable_dsm_is_rejected(self) -> None:
        product = full_dsm(
            issued=datetime(2026, 8, 22, 5, 30, tzinfo=UTC),
            text="KNYC DS UNKNOWN",
        )
        self.assertEqual(product.lifecycle, ValidationLifecycle.REJECTED)
        self.assertIsNone(product.climate_date)
        self.assertIsNone(product.reported_max_f)


class CliLifecycleTests(unittest.TestCase):
    def test_same_climate_day_cli_is_preliminary(self) -> None:
        product = cli(issued=datetime(2026, 8, 21, 21, 0, tzinfo=UTC))
        self.assertEqual(product.climate_date, DAY)
        self.assertEqual(product.reported_max_f, 77)
        self.assertEqual(product.lifecycle, ValidationLifecycle.CURRENT_DAY_PRELIMINARY)
        self.assertEqual(product.authority, ValidationAuthority.CORROBORATION_ONLY)

    def test_next_day_cli_is_completed_day_but_still_preliminary(self) -> None:
        product = cli(issued=datetime(2026, 8, 22, 5, 30, tzinfo=UTC))
        self.assertEqual(product.lifecycle, ValidationLifecycle.COMPLETED_DAY_PRELIMINARY)
        self.assertNotEqual(product.lifecycle, ValidationLifecycle.AUTHORITATIVE_FINAL)
        self.assertEqual(product.authority, ValidationAuthority.CORROBORATION_ONLY)

    def test_missing_explicit_cli_date_fails_closed(self) -> None:
        product = cli(
            issued=datetime(2026, 8, 22, 5, 30, tzinfo=UTC),
            text="CLIMATE REPORT\nTEMPERATURE (F)\nMAXIMUM 77\n",
        )
        self.assertEqual(product.lifecycle, ValidationLifecycle.REJECTED)
        self.assertEqual(product.fail_closed_reason, "cli_explicit_report_date_missing")

    def test_future_cli_target_is_ambiguous(self) -> None:
        product = cli(
            issued=datetime(2026, 8, 21, 21, 0, tzinfo=UTC),
            report_day=22,
        )
        self.assertEqual(product.lifecycle, ValidationLifecycle.AMBIGUOUS)
        self.assertFalse(product.accepted_validation)


class ValidationTrustBoundaryTests(unittest.TestCase):
    def test_nws_validation_evidence_is_never_benchmark_eligible(self) -> None:
        product = cli(issued=datetime(2026, 8, 22, 5, 30, tzinfo=UTC))
        evidence = product.to_validation_evidence()
        self.assertEqual(evidence.trust, EvidenceTrust.VALIDATION_ONLY)
        self.assertFalse(evidence.benchmark_eligible)
        timeline = accumulate_hard_state(
            [evidence],
            station_code="KNYC",
            climate_date=DAY,
        )
        self.assertIsNone(timeline.current_state)

    def test_validation_round_trip_is_deterministic(self) -> None:
        product = full_dsm(issued=datetime(2026, 8, 22, 5, 30, tzinfo=UTC))
        restored = ValidationProduct.from_dict(product.to_dict())
        self.assertEqual(product, restored)
        self.assertEqual(product.validation_id, restored.validation_id)


class AuthoritativeSettlementTests(unittest.TestCase):
    def test_matching_rule_source_builds_authoritative_truth(self) -> None:
        settlement = build_authoritative_settlement(
            event_ticker="KXHIGHNY-26AUG21",
            station_code="KNYC",
            climate_day=DAY,
            final_max_f=77,
            source_record_id="raw_source_journal:200",
            observed_or_issued_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
            rules_hash="c" * 64,
            rule_source_name="The Weather Company",
            settlement_source_name="The Weather Company",
        )
        self.assertEqual(settlement.authority, ValidationAuthority.CONTRACT_AUTHORITATIVE)
        self.assertEqual(settlement.truth.status, "authoritative_final")
        self.assertEqual(settlement.truth.final_max_f, 77)
        self.assertEqual(settlement.truth.climate_date, DAY)
        self.assertEqual(AuthoritativeSettlement.from_dict(settlement.to_dict()), settlement)

    def test_mismatched_rule_source_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            build_authoritative_settlement(
                event_ticker="KXHIGHNY-26AUG21",
                station_code="KNYC",
                climate_day=DAY,
                final_max_f=77,
                source_record_id="raw_source_journal:200",
                observed_or_issued_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
                rules_hash="c" * 64,
                rule_source_name="The Weather Company",
                settlement_source_name="National Weather Service",
            )

    def test_wrong_event_date_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            build_authoritative_settlement(
                event_ticker="KXHIGHNY-26AUG22",
                station_code="KNYC",
                climate_day=DAY,
                final_max_f=77,
                source_record_id="raw_source_journal:200",
                observed_or_issued_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
                rules_hash="c" * 64,
                rule_source_name="The Weather Company",
                settlement_source_name="The Weather Company",
            )

    def test_exchange_result_is_explicit_authority_class(self) -> None:
        settlement = build_authoritative_settlement(
            event_ticker="KXHIGHNY-26AUG21",
            station_code="KNYC",
            climate_day=DAY,
            final_max_f=77,
            source_record_id="raw_source_journal:201",
            observed_or_issued_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
            rules_hash="d" * 64,
            rule_source_name="The Weather Company",
            settlement_source_name="Kalshi exchange result",
            exchange_result=True,
        )
        self.assertEqual(settlement.authority, ValidationAuthority.EXCHANGE_RESULT)
        self.assertEqual(settlement.truth.source, "KALSHI_EXCHANGE_RESULT")


if __name__ == "__main__":
    unittest.main()
