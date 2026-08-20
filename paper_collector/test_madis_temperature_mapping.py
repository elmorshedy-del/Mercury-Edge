from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from madis_omo import MadisMinuteStatus, MadisOmoMinute
from madis_temperature_mapping import (
    MadisKelvinEncodingPolicy,
    MinuteMappingStatus,
    SourceRoundingRule,
    forward_encode_f,
    inverse_candidates_f,
    map_madis_minute,
)

UTC = timezone.utc


def policy(
    resolution: str = "0.01",
    *,
    version: str = "synthetic-policy-v1",
    rounding: SourceRoundingRule = SourceRoundingRule.HALF_UP,
) -> MadisKelvinEncodingPolicy:
    return MadisKelvinEncodingPolicy(
        resolution_k=Decimal(resolution),
        rounding_rule=rounding,
        min_f=-100,
        max_f=140,
        policy_version=version,
    )


def minute(kelvin: Decimal, *, status: MadisMinuteStatus = MadisMinuteStatus.ACCEPTED_RESEARCH) -> MadisOmoMinute:
    observed = datetime(2026, 8, 18, 18, 41, tzinfo=UTC)
    received = datetime(2026, 8, 18, 18, 41, 6, tzinfo=UTC)
    interpreted = datetime(2026, 8, 18, 18, 41, 6, 250000, tzinfo=UTC)
    return MadisOmoMinute(
        minute_id="madis-minute:1",
        raw_record_id="raw_source_journal:1",
        station_code="KLAX",
        climate_date=date(2026, 8, 18),
        observed_at=observed,
        source_published_at=datetime(2026, 8, 18, 18, 41, 4, tzinfo=UTC),
        first_fetchable_at=datetime(2026, 8, 18, 18, 41, 5, tzinfo=UTC),
        ldm_received_at=received,
        mercury_interpreted_at=interpreted,
        temperature=kelvin,
        temperature_unit="K",
        upstream_variable="T",
        temperature_sensor_status=0,
        qc_status=None,
        sequence_key="c:1",
        status=status,
        parser_version="madis-omo-adapter-contract-v1",
        calendar_version="lst-climate-calendar-v1",
    )


class MadisKelvinInverseLatticeTests(unittest.TestCase):
    def test_unique_synthetic_lattice_point_returns_one_integer_f(self) -> None:
        p = policy("0.01")
        encoded = forward_encode_f(88, p)
        self.assertEqual(encoded, Decimal("304.26"))
        self.assertEqual(inverse_candidates_f(encoded, p), (88,))
        result = map_madis_minute(minute(encoded), p)
        self.assertEqual(result.status, MinuteMappingStatus.UNIQUE_RESEARCH)
        self.assertEqual(result.unique_f, 88)
        self.assertTrue(result.reconstruction_usable)
        self.assertFalse(result.to_dict()["benchmark_eligible"])

    def test_coarse_policy_can_be_ambiguous_and_is_not_reconstruction_usable(self) -> None:
        p = policy("1")
        encoded = forward_encode_f(88, p)
        candidates = inverse_candidates_f(encoded, p)
        self.assertGreater(len(candidates), 1)
        result = map_madis_minute(minute(encoded), p)
        self.assertEqual(result.status, MinuteMappingStatus.AMBIGUOUS)
        self.assertEqual(result.candidates_f, candidates)
        self.assertIsNone(result.unique_f)
        self.assertFalse(result.reconstruction_usable)

    def test_off_policy_kelvin_value_fails_closed_instead_of_rounding_to_f(self) -> None:
        p = policy("0.01")
        result = map_madis_minute(minute(Decimal("304.261")), p)
        self.assertEqual(result.status, MinuteMappingStatus.OFF_POLICY_VALUE)
        self.assertIsNone(result.unique_f)
        self.assertEqual(result.fail_closed_reason, "kelvin_value_not_on_configured_source_lattice")

    def test_unknown_source_encoding_policy_fails_closed(self) -> None:
        result = map_madis_minute(minute(Decimal("304.26")), None)
        self.assertEqual(result.status, MinuteMappingStatus.UNVERIFIED_SOURCE_ENCODING)
        self.assertFalse(result.reconstruction_usable)

    def test_bad_upstream_minute_cannot_be_mapped_even_on_valid_lattice(self) -> None:
        p = policy()
        encoded = forward_encode_f(77, p)
        result = map_madis_minute(minute(encoded, status=MadisMinuteStatus.QC_REJECTED), p)
        self.assertEqual(result.status, MinuteMappingStatus.SOURCE_NOT_RESEARCH_USABLE)
        self.assertFalse(result.reconstruction_usable)

    def test_policy_version_changes_derivation_identity(self) -> None:
        encoded = forward_encode_f(88, policy(version="p1"))
        first = map_madis_minute(minute(encoded), policy(version="p1"))
        second = map_madis_minute(minute(encoded), policy(version="p2"))
        self.assertNotEqual(first.derivation_id, second.derivation_id)
        self.assertEqual(first.unique_f, second.unique_f)

    def test_rounding_rule_is_part_of_explicit_policy_identity(self) -> None:
        p1 = policy(version="same", rounding=SourceRoundingRule.HALF_UP)
        p2 = policy(version="same", rounding=SourceRoundingRule.HALF_EVEN)
        encoded = forward_encode_f(77, p1)
        first = map_madis_minute(minute(encoded), p1)
        second = map_madis_minute(minute(encoded), p2)
        self.assertNotEqual(first.derivation_id, second.derivation_id)

    def test_mapping_is_deterministic(self) -> None:
        p = policy()
        m = minute(forward_encode_f(89, p))
        self.assertEqual(map_madis_minute(m, p), map_madis_minute(m, p))

    def test_invalid_policy_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            policy("0")
        with self.assertRaises(ValueError):
            MadisKelvinEncodingPolicy(
                resolution_k=Decimal("0.01"),
                rounding_rule=SourceRoundingRule.HALF_UP,
                min_f=100,
                max_f=0,
                policy_version="x",
            )


if __name__ == "__main__":
    unittest.main()
