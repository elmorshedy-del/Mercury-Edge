from __future__ import annotations

"""Parse raw US ASOS/METAR temperature evidence without Celsius→Fahrenheit guesswork.

ASOS climate temperature is Fahrenheit-native: the rolling temperature is rounded
and stored as a whole degree Fahrenheit, then encoded for METAR transmission in
Celsius. For hard-state trading we therefore invert the *documented forward
encoding lattice* instead of converting decoded Celsius back to a continuous
Fahrenheit number and rounding it.

This module is intentionally pure: it does not know about databases, Kalshi, or
portfolio logic. It turns raw METAR groups into bounded canonical-Fahrenheit
possibilities that later code can use as proof objects.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Iterable

TENTH = Decimal("0.1")
ONE = Decimal("1")

# Broad physical envelope, intentionally much wider than any current Mercury
# station. The exact limits are not a trading assumption; they only bound the
# finite inverse lattice.
MIN_CANONICAL_F = -150
MAX_CANONICAL_F = 160

_T_GROUP_RE = re.compile(r"\bT(?P<tsign>[01])(?P<temp>\d{3})(?P<dsign>[01])(?P<dew>\d{3})\b")
_SIX_HOUR_MAX_RE = re.compile(r"\b1(?P<sign>[01])(?P<temp>\d{3})\b")
_24H_RE = re.compile(r"\b4(?P<max_sign>[01])(?P<max_temp>\d{3})(?P<min_sign>[01])(?P<min_temp>\d{3})\b")
# Main METAR temperature/dewpoint group, e.g. 31/20, M05/M12. The negative
# lookbehind prevents matching the tail of a longer numeric remark group.
_MAIN_RE = re.compile(r"(?<![A-Z0-9])(?P<temp>M?\d{2})/(?P<dew>M?\d{2}|//)(?![A-Z0-9])")


@dataclass(frozen=True)
class TemperatureEvidence:
    kind: str
    raw_group: str
    encoded_c: Decimal
    possible_canonical_f: tuple[int, ...]
    integrity_status: str
    hard_state_eligible: bool
    note: str | None = None

    @property
    def proven_min_f(self) -> int | None:
        return min(self.possible_canonical_f) if self.possible_canonical_f else None

    @property
    def proven_max_f(self) -> int | None:
        return max(self.possible_canonical_f) if self.possible_canonical_f else None

    @property
    def exact_canonical_f(self) -> int | None:
        if len(self.possible_canonical_f) == 1:
            return self.possible_canonical_f[0]
        return None

    def proves_above(self, upper_bound_f: int | Decimal) -> bool:
        """True only when every canonical °F state compatible with this raw group is above a bound."""
        if not self.hard_state_eligible or self.proven_min_f is None:
            return False
        return Decimal(self.proven_min_f) > Decimal(str(upper_bound_f))


def _round_tenth_c(value: Decimal) -> Decimal:
    return value.quantize(TENTH, rounding=ROUND_HALF_UP)


def _round_whole_c(value: Decimal) -> Decimal:
    return value.quantize(ONE, rounding=ROUND_HALF_UP)


def canonical_f_to_tenths_c(fahrenheit: int) -> Decimal:
    """Documented ASOS forward encoding: whole °F -> nearest 0.1 °C."""
    f = Decimal(fahrenheit)
    return _round_tenth_c((f - Decimal(32)) * Decimal(5) / Decimal(9))


def canonical_f_to_main_c(fahrenheit: int) -> Decimal:
    """Main METAR whole-°C field derived from the canonical whole-°F lattice."""
    return _round_whole_c(canonical_f_to_tenths_c(fahrenheit))


def _build_inverse_lattice() -> tuple[dict[Decimal, tuple[int, ...]], dict[Decimal, tuple[int, ...]]]:
    tenths: dict[Decimal, list[int]] = {}
    whole: dict[Decimal, list[int]] = {}
    for fahrenheit in range(MIN_CANONICAL_F, MAX_CANONICAL_F + 1):
        t = canonical_f_to_tenths_c(fahrenheit)
        tenths.setdefault(t, []).append(fahrenheit)
        w = canonical_f_to_main_c(fahrenheit)
        whole.setdefault(w, []).append(fahrenheit)
    return (
        {key: tuple(values) for key, values in tenths.items()},
        {key: tuple(values) for key, values in whole.items()},
    )


_TENTHS_C_TO_F, _WHOLE_C_TO_F = _build_inverse_lattice()


def _signed_tenths(sign: str, digits: str) -> Decimal:
    value = Decimal(int(digits)) / Decimal(10)
    return -value if sign == "1" else value


def _main_c(token: str) -> Decimal:
    return Decimal(-int(token[1:])) if token.startswith("M") else Decimal(int(token))


def evidence_from_tenths(kind: str, raw_group: str, encoded_c: Decimal) -> TemperatureEvidence:
    possible = _TENTHS_C_TO_F.get(encoded_c, ())
    if not possible:
        return TemperatureEvidence(
            kind=kind,
            raw_group=raw_group,
            encoded_c=encoded_c,
            possible_canonical_f=(),
            integrity_status="off_lattice",
            hard_state_eligible=False,
            note="Tenths-C value is not an image of any canonical whole-F ASOS state.",
        )
    return TemperatureEvidence(
        kind=kind,
        raw_group=raw_group,
        encoded_c=encoded_c,
        possible_canonical_f=possible,
        integrity_status="canonical" if len(possible) == 1 else "ambiguous_lattice",
        hard_state_eligible=True,
    )


def evidence_from_main_c(raw_group: str, encoded_c: Decimal) -> TemperatureEvidence:
    possible = _WHOLE_C_TO_F.get(encoded_c, ())
    if not possible:
        return TemperatureEvidence(
            kind="main_temp_c",
            raw_group=raw_group,
            encoded_c=encoded_c,
            possible_canonical_f=(),
            integrity_status="off_lattice",
            hard_state_eligible=False,
            note="Whole-C field cannot be mapped to the configured canonical-F lattice.",
        )
    return TemperatureEvidence(
        kind="main_temp_c",
        raw_group=raw_group,
        encoded_c=encoded_c,
        possible_canonical_f=possible,
        integrity_status="ambiguous_lattice" if len(possible) > 1 else "canonical",
        hard_state_eligible=True,
        note="Lossy main METAR field; use only its proven Fahrenheit bounds, never round-trip Celsius.",
    )


def parse_temperature_evidence(raw_metar: str) -> list[TemperatureEvidence]:
    """Extract temperature proof objects from one raw METAR/SPECI string.

    Returned items are independent evidence records. No daily-window semantics
    are applied here; six-hour and 24-hour group windows are handled by the next
    calendar integration step.
    """
    text = " ".join(str(raw_metar).strip().split())
    out: list[TemperatureEvidence] = []

    main = _MAIN_RE.search(text)
    if main:
        raw = main.group(0)
        out.append(evidence_from_main_c(raw, _main_c(main.group("temp"))))

    t_group = _T_GROUP_RE.search(text)
    if t_group:
        raw = t_group.group(0)
        out.append(
            evidence_from_tenths(
                "t_group",
                raw,
                _signed_tenths(t_group.group("tsign"), t_group.group("temp")),
            )
        )

    # There can be many numeric remark groups. Anchored whitespace/token regexes
    # above keep the six-hour max distinct from the 24-hour group and T-group.
    for match in _SIX_HOUR_MAX_RE.finditer(text):
        raw = match.group(0)
        out.append(
            evidence_from_tenths(
                "six_hour_max",
                raw,
                _signed_tenths(match.group("sign"), match.group("temp")),
            )
        )

    for match in _24H_RE.finditer(text):
        raw = match.group(0)
        out.append(
            evidence_from_tenths(
                "twenty_four_hour_max",
                raw,
                _signed_tenths(match.group("max_sign"), match.group("max_temp")),
            )
        )

    return out


def evidence_by_kind(items: Iterable[TemperatureEvidence], kind: str) -> list[TemperatureEvidence]:
    return [item for item in items if item.kind == kind]
