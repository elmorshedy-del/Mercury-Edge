from decimal import Decimal
import unittest

from asos_evidence import (
    canonical_f_to_main_c,
    canonical_f_to_tenths_c,
    evidence_by_kind,
    evidence_from_main_c,
    evidence_from_tenths,
    parse_temperature_evidence,
)


class AsosLatticeTests(unittest.TestCase):
    def test_known_fahrenheit_lattice(self) -> None:
        expected = {
            85: (Decimal("29.4"), Decimal("29")),
            86: (Decimal("30.0"), Decimal("30")),
            87: (Decimal("30.6"), Decimal("31")),
            88: (Decimal("31.1"), Decimal("31")),
            89: (Decimal("31.7"), Decimal("32")),
            90: (Decimal("32.2"), Decimal("32")),
        }
        for fahrenheit, (tenths_c, main_c) in expected.items():
            self.assertEqual(canonical_f_to_tenths_c(fahrenheit), tenths_c)
            self.assertEqual(canonical_f_to_main_c(fahrenheit), main_c)

    def test_t0311_uniquely_means_88f(self) -> None:
        evidence = evidence_from_tenths("t_group", "T0311", Decimal("31.1"))
        self.assertEqual(evidence.possible_canonical_f, (88,))
        self.assertEqual(evidence.exact_canonical_f, 88)
        self.assertTrue(evidence.proves_above(87))

    def test_t0306_is_87f_and_does_not_kill_86_87_bucket(self) -> None:
        evidence = evidence_from_tenths("t_group", "T0306", Decimal("30.6"))
        self.assertEqual(evidence.possible_canonical_f, (87,))
        self.assertFalse(evidence.proves_above(87))

    def test_t0310_is_off_lattice_and_fails_closed(self) -> None:
        evidence = evidence_from_tenths("t_group", "T0310", Decimal("31.0"))
        self.assertEqual(evidence.integrity_status, "off_lattice")
        self.assertEqual(evidence.possible_canonical_f, ())
        self.assertFalse(evidence.hard_state_eligible)
        self.assertFalse(evidence.proves_above(87))

    def test_main_31c_is_ambiguous_87_or_88(self) -> None:
        evidence = evidence_from_main_c("31/20", Decimal("31"))
        self.assertEqual(evidence.possible_canonical_f, (87, 88))
        self.assertEqual(evidence.proven_min_f, 87)
        self.assertEqual(evidence.proven_max_f, 88)
        self.assertFalse(evidence.proves_above(87))

    def test_main_32c_proves_above_87(self) -> None:
        evidence = evidence_from_main_c("32/20", Decimal("32"))
        self.assertEqual(evidence.possible_canonical_f, (89, 90))
        self.assertTrue(evidence.proves_above(87))


class RawMetarParsingTests(unittest.TestCase):
    def test_full_t_group_extracts_temperature_half(self) -> None:
        items = parse_temperature_evidence(
            "KPHL 181854Z 22008KT 10SM FEW050 31/20 A2992 RMK AO2 T03110200"
        )
        t = evidence_by_kind(items, "t_group")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].exact_canonical_f, 88)
        main = evidence_by_kind(items, "main_temp_c")
        self.assertEqual(main[0].possible_canonical_f, (87, 88))

    def test_six_hour_max_10311_is_88f(self) -> None:
        items = parse_temperature_evidence(
            "KPHL 182354Z 22008KT 10SM CLR 29/20 A2992 RMK AO2 10311 20200 T02940200"
        )
        six = evidence_by_kind(items, "six_hour_max")
        self.assertEqual(len(six), 1)
        self.assertEqual(six[0].exact_canonical_f, 88)
        self.assertTrue(six[0].proves_above(87))

    def test_klax_24_hour_group_25c_is_77f(self) -> None:
        items = parse_temperature_evidence(
            "KLAX 171053Z 25006KT 10SM CLR 20/17 A2995 RMK AO2 402500194 T02000167"
        )
        daily = evidence_by_kind(items, "twenty_four_hour_max")
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0].exact_canonical_f, 77)

    def test_off_lattice_raw_t_group_is_rejected(self) -> None:
        items = parse_temperature_evidence(
            "KPHL 181854Z 22008KT 10SM CLR 31/20 A2992 RMK AO2 T03100200"
        )
        t = evidence_by_kind(items, "t_group")[0]
        self.assertEqual(t.integrity_status, "off_lattice")
        self.assertFalse(t.hard_state_eligible)

    def test_negative_main_temperature(self) -> None:
        items = parse_temperature_evidence(
            "KDEN 011653Z 01005KT 10SM CLR M05/M12 A3010 RMK AO2 T10501117"
        )
        main = evidence_by_kind(items, "main_temp_c")[0]
        self.assertLessEqual(main.proven_min_f, 23)
        self.assertGreaterEqual(main.proven_max_f, 23)
        t = evidence_by_kind(items, "t_group")[0]
        self.assertEqual(t.exact_canonical_f, 23)


if __name__ == "__main__":
    unittest.main()
