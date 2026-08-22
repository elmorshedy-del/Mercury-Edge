from __future__ import annotations

"""Step 4H validation/settlement lifecycle normalization.

This module is deliberately source/lifecycle oriented and has no trading logic.
NWS DSM/CLI products are validation inputs only. Authoritative contract
settlement is constructed separately and only with explicit event/rule-source
provenance.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from hard_information_domain import (
    EvidenceTrust,
    EvidenceType,
    IntegrityStatus,
    SettlementEvidence,
    SettlementTruth,
    SourceClocks,
)
from market_calendar import (
    CLIMATE_CALENDAR_VERSION,
    climate_date,
    climate_day_bounds,
    event_trade_date,
    local_standard_time,
)

VALIDATION_MODEL_VERSION = "validation-lifecycle-v1"
DSM_PARSER_VERSION = "nws-dsm-validation-v1"
CLI_PARSER_VERSION = "nws-cli-validation-v1"
SETTLEMENT_AUTHORITY_VERSION = "settlement-authority-v1"


class ValidationLifecycle(str, Enum):
    CURRENT_DAY_PRELIMINARY = "current_day_preliminary"
    COMPLETED_DAY_PRELIMINARY = "completed_day_preliminary"
    AUTHORITATIVE_FINAL = "authoritative_final"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


class ValidationAuthority(str, Enum):
    CORROBORATION_ONLY = "corroboration_only"
    CONTRACT_AUTHORITATIVE = "contract_authoritative"
    EXCHANGE_RESULT = "exchange_result"


@dataclass(frozen=True)
class ValidationProduct:
    validation_id: str
    source: str
    source_product_id: str
    station_code: str
    climate_date: date | None
    reported_max_f: int | None
    max_observed_at: datetime | None
    issued_at: datetime
    mercury_received_at: datetime
    source_record_id: str
    source_payload_sha256: str
    lifecycle: ValidationLifecycle
    authority: ValidationAuthority
    parser_version: str
    validation_model_version: str = VALIDATION_MODEL_VERSION
    calendar_version: str = CLIMATE_CALENDAR_VERSION
    corrected: bool = False
    revision_of: str | None = None
    fail_closed_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def accepted_validation(self) -> bool:
        return (
            self.lifecycle
            in {
                ValidationLifecycle.CURRENT_DAY_PRELIMINARY,
                ValidationLifecycle.COMPLETED_DAY_PRELIMINARY,
            }
            and self.climate_date is not None
            and self.reported_max_f is not None
            and self.fail_closed_reason is None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "source": self.source,
            "source_product_id": self.source_product_id,
            "station_code": self.station_code,
            "climate_date": self.climate_date.isoformat() if self.climate_date else None,
            "reported_max_f": self.reported_max_f,
            "max_observed_at": _iso(self.max_observed_at),
            "issued_at": _iso(self.issued_at),
            "mercury_received_at": _iso(self.mercury_received_at),
            "source_record_id": self.source_record_id,
            "source_payload_sha256": self.source_payload_sha256,
            "lifecycle": self.lifecycle.value,
            "authority": self.authority.value,
            "parser_version": self.parser_version,
            "validation_model_version": self.validation_model_version,
            "calendar_version": self.calendar_version,
            "corrected": self.corrected,
            "revision_of": self.revision_of,
            "fail_closed_reason": self.fail_closed_reason,
            "metadata": _jsonable(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationProduct":
        raw_date = value.get("climate_date")
        return cls(
            validation_id=str(value["validation_id"]),
            source=str(value["source"]),
            source_product_id=str(value["source_product_id"]),
            station_code=str(value["station_code"]),
            climate_date=date.fromisoformat(str(raw_date)) if raw_date else None,
            reported_max_f=(int(value["reported_max_f"]) if value.get("reported_max_f") is not None else None),
            max_observed_at=_dt(value.get("max_observed_at")),
            issued_at=_required_dt(value["issued_at"]),
            mercury_received_at=_required_dt(value["mercury_received_at"]),
            source_record_id=str(value["source_record_id"]),
            source_payload_sha256=str(value["source_payload_sha256"]),
            lifecycle=ValidationLifecycle(str(value["lifecycle"])),
            authority=ValidationAuthority(str(value["authority"])),
            parser_version=str(value["parser_version"]),
            validation_model_version=str(value.get("validation_model_version", VALIDATION_MODEL_VERSION)),
            calendar_version=str(value.get("calendar_version", CLIMATE_CALENDAR_VERSION)),
            corrected=bool(value.get("corrected", False)),
            revision_of=(str(value["revision_of"]) if value.get("revision_of") is not None else None),
            fail_closed_reason=(str(value["fail_closed_reason"]) if value.get("fail_closed_reason") else None),
            metadata=dict(value.get("metadata") or {}),
        )

    def to_validation_evidence(self) -> SettlementEvidence:
        evidence_type = EvidenceType.DSM_MAX if self.source == "NWS_DSM" else EvidenceType.CLI_MAX
        usable = self.accepted_validation
        integrity = IntegrityStatus.CANONICAL if usable else IntegrityStatus.INCOMPLETE
        trust = EvidenceTrust.VALIDATION_ONLY if usable else EvidenceTrust.REJECTED
        evidence_id = _stable_id(
            "validation-evidence",
            self.validation_id,
            self.validation_model_version,
            self.parser_version,
        )
        return SettlementEvidence(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            station_code=self.station_code,
            climate_date=self.climate_date or climate_date(self.issued_at, str(self.metadata.get("timezone_name") or "UTC")),
            source_record_ids=(self.source_record_id,),
            proven_min_f=self.reported_max_f if usable else None,
            proven_max_f=self.reported_max_f if usable else None,
            integrity_status=integrity,
            trust=trust,
            clocks=SourceClocks(
                observed_at=self.max_observed_at or self.issued_at,
                source_published_at=self.issued_at,
                first_fetchable_at=None,
                mercury_received_at=self.mercury_received_at,
                mercury_interpreted_at=self.mercury_received_at,
            ),
            parser_version=self.parser_version,
            evidence_model_version=self.validation_model_version,
            calendar_version=self.calendar_version,
            raw_identifier=self.source_product_id,
            possible_canonical_f=((self.reported_max_f,) if usable and self.reported_max_f is not None else ()),
            fail_closed_reason=self.fail_closed_reason,
            metadata={
                "validation_only": True,
                "lifecycle": self.lifecycle.value,
                "authority": self.authority.value,
                "source_payload_sha256": self.source_payload_sha256,
            },
        )


@dataclass(frozen=True)
class AuthoritativeSettlement:
    settlement_id: str
    event_ticker: str
    rules_hash: str
    rule_source_name: str
    settlement_source_name: str
    authority: ValidationAuthority
    truth: SettlementTruth
    parser_version: str = SETTLEMENT_AUTHORITY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "event_ticker": self.event_ticker,
            "rules_hash": self.rules_hash,
            "rule_source_name": self.rule_source_name,
            "settlement_source_name": self.settlement_source_name,
            "authority": self.authority.value,
            "truth": self.truth.to_dict(),
            "parser_version": self.parser_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuthoritativeSettlement":
        return cls(
            settlement_id=str(value["settlement_id"]),
            event_ticker=str(value["event_ticker"]),
            rules_hash=str(value["rules_hash"]),
            rule_source_name=str(value["rule_source_name"]),
            settlement_source_name=str(value["settlement_source_name"]),
            authority=ValidationAuthority(str(value["authority"])),
            truth=SettlementTruth.from_dict(dict(value["truth"])),
            parser_version=str(value.get("parser_version", SETTLEMENT_AUTHORITY_VERSION)),
        )


_DSM_LINE_RE_TEMPLATE = (
    r"(?mi)^\s*{station}\s+DS\s+"
    r"(?:(?P<cor>COR)\s+)?"
    r"(?:(?P<cutoff>\d{{4}})\s+)?"
    r"(?P<day>\d{{2}})/(?P<month>\d{{2}})\s+"
    r"(?P<high>M?\d{{2,3}})(?P<high_time>\d{{4}})/"
)

_CLI_DATE_RE = re.compile(
    r"(?im)\bCLIMATE\s+SUMMARY\s+FOR\s+"
    r"(?P<month>JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+"
    r"(?P<day>\d{1,2})\s+(?P<year>\d{4})\b"
)
_CLI_MAX_RE = re.compile(r"(?im)^\s*MAXIMUM\s+(?P<high>M?-?\d{1,3})\b")
_MONTHS = {
    "JANUARY": 1,
    "FEBRUARY": 2,
    "MARCH": 3,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DECEMBER": 12,
}


def parse_nws_dsm(
    raw_text: str,
    *,
    source_product_id: str,
    station_code: str,
    timezone_name: str,
    issued_at: datetime,
    mercury_received_at: datetime,
    source_record_id: str,
    source_payload_sha256: str,
    revision_of: str | None = None,
) -> ValidationProduct:
    station = station_code.strip().upper()
    pattern = re.compile(_DSM_LINE_RE_TEMPLATE.format(station=re.escape(station)))
    match = pattern.search(raw_text)
    base = {
        "source": "NWS_DSM",
        "source_product_id": source_product_id,
        "station_code": station,
        "issued_at": issued_at,
        "mercury_received_at": mercury_received_at,
        "source_record_id": source_record_id,
        "source_payload_sha256": source_payload_sha256,
        "authority": ValidationAuthority.CORROBORATION_ONLY,
        "parser_version": DSM_PARSER_VERSION,
        "revision_of": revision_of,
        "metadata": {"timezone_name": timezone_name},
    }
    if not match:
        return _validation_product(
            **base,
            climate_day=None,
            reported_max_f=None,
            max_observed_at=None,
            lifecycle=ValidationLifecycle.REJECTED,
            corrected=False,
            fail_closed_reason="dsm_station_summary_line_not_parseable",
        )

    try:
        high_f = _signed_int(match.group("high"))
        high_hour, high_minute = _hhmm(match.group("high_time"))
        cutoff = match.group("cutoff")
        if cutoff is not None:
            _hhmm(cutoff)
        target = _infer_dsm_date(
            day=int(match.group("day")),
            month=int(match.group("month")),
            issued_at=issued_at,
            timezone_name=timezone_name,
        )
    except (TypeError, ValueError):
        return _validation_product(
            **base,
            climate_day=None,
            reported_max_f=None,
            max_observed_at=None,
            lifecycle=ValidationLifecycle.REJECTED,
            corrected=bool(match.group("cor")),
            fail_closed_reason="dsm_date_temperature_or_time_invalid",
        )

    issued_climate_day = climate_date(issued_at, timezone_name)
    if cutoff is not None:
        lifecycle = (
            ValidationLifecycle.CURRENT_DAY_PRELIMINARY
            if target <= issued_climate_day
            else ValidationLifecycle.AMBIGUOUS
        )
        fail_reason = None if lifecycle is ValidationLifecycle.CURRENT_DAY_PRELIMINARY else "dsm_partial_target_after_issue_climate_date"
    else:
        lifecycle = (
            ValidationLifecycle.COMPLETED_DAY_PRELIMINARY
            if target < issued_climate_day
            else ValidationLifecycle.AMBIGUOUS
        )
        fail_reason = None if lifecycle is ValidationLifecycle.COMPLETED_DAY_PRELIMINARY else "dsm_completed_form_not_for_prior_climate_date"

    start_utc, _ = climate_day_bounds(target, timezone_name)
    max_at = start_utc + timedelta(hours=high_hour, minutes=high_minute)
    return _validation_product(
        **base,
        climate_day=target,
        reported_max_f=high_f,
        max_observed_at=max_at,
        lifecycle=lifecycle,
        corrected=bool(match.group("cor")),
        fail_closed_reason=fail_reason,
        metadata={
            "timezone_name": timezone_name,
            "partial_cutoff_lst": cutoff,
            "raw_high_time_lst": match.group("high_time"),
        },
    )


def parse_nws_cli(
    raw_text: str,
    *,
    source_product_id: str,
    station_code: str,
    timezone_name: str,
    issued_at: datetime,
    mercury_received_at: datetime,
    source_record_id: str,
    source_payload_sha256: str,
    revision_of: str | None = None,
) -> ValidationProduct:
    station = station_code.strip().upper()
    date_match = _CLI_DATE_RE.search(raw_text)
    high_match = _CLI_MAX_RE.search(raw_text)
    base = {
        "source": "NWS_CLI",
        "source_product_id": source_product_id,
        "station_code": station,
        "issued_at": issued_at,
        "mercury_received_at": mercury_received_at,
        "source_record_id": source_record_id,
        "source_payload_sha256": source_payload_sha256,
        "authority": ValidationAuthority.CORROBORATION_ONLY,
        "parser_version": CLI_PARSER_VERSION,
        "revision_of": revision_of,
        "metadata": {"timezone_name": timezone_name},
    }
    if not date_match:
        return _validation_product(
            **base,
            climate_day=None,
            reported_max_f=None,
            max_observed_at=None,
            lifecycle=ValidationLifecycle.REJECTED,
            corrected=False,
            fail_closed_reason="cli_explicit_report_date_missing",
        )
    if not high_match:
        return _validation_product(
            **base,
            climate_day=None,
            reported_max_f=None,
            max_observed_at=None,
            lifecycle=ValidationLifecycle.REJECTED,
            corrected=False,
            fail_closed_reason="cli_maximum_missing",
        )

    try:
        target = date(
            int(date_match.group("year")),
            _MONTHS[date_match.group("month").upper()],
            int(date_match.group("day")),
        )
        high_f = _signed_int(high_match.group("high"))
    except (KeyError, TypeError, ValueError):
        return _validation_product(
            **base,
            climate_day=None,
            reported_max_f=None,
            max_observed_at=None,
            lifecycle=ValidationLifecycle.REJECTED,
            corrected=False,
            fail_closed_reason="cli_date_or_maximum_invalid",
        )

    issue_day = climate_date(issued_at, timezone_name)
    if target < issue_day:
        lifecycle = ValidationLifecycle.COMPLETED_DAY_PRELIMINARY
        fail_reason = None
    elif target == issue_day:
        lifecycle = ValidationLifecycle.CURRENT_DAY_PRELIMINARY
        fail_reason = None
    else:
        lifecycle = ValidationLifecycle.AMBIGUOUS
        fail_reason = "cli_target_after_issue_climate_date"

    return _validation_product(
        **base,
        climate_day=target,
        reported_max_f=high_f,
        max_observed_at=None,
        lifecycle=lifecycle,
        corrected=False,
        fail_closed_reason=fail_reason,
    )


def build_authoritative_settlement(
    *,
    event_ticker: str,
    station_code: str,
    climate_day: date,
    final_max_f: int,
    source_record_id: str,
    observed_or_issued_at: datetime,
    rules_hash: str,
    rule_source_name: str,
    settlement_source_name: str,
    exchange_result: bool = False,
    revision_of: str | None = None,
) -> AuthoritativeSettlement:
    event_day = event_trade_date(event_ticker)
    if event_day is None or event_day != climate_day:
        raise ValueError("event ticker date does not match settlement climate date")
    if not station_code.strip():
        raise ValueError("station code is required")
    if not rules_hash.strip():
        raise ValueError("rules hash is required")

    authority = ValidationAuthority.EXCHANGE_RESULT if exchange_result else ValidationAuthority.CONTRACT_AUTHORITATIVE
    if not exchange_result and _source_key(rule_source_name) != _source_key(settlement_source_name):
        raise ValueError("settlement source does not match captured rule source")

    truth_id = _stable_id(
        "settlement-truth",
        event_ticker,
        station_code.upper(),
        climate_day.isoformat(),
        str(final_max_f),
        source_record_id,
        rules_hash,
        rule_source_name,
        settlement_source_name,
        authority.value,
        SETTLEMENT_AUTHORITY_VERSION,
    )
    truth = SettlementTruth(
        truth_id=truth_id,
        source=("KALSHI_EXCHANGE_RESULT" if exchange_result else settlement_source_name),
        station_code=station_code.upper(),
        climate_date=climate_day,
        final_max_f=int(final_max_f),
        status="authoritative_final",
        source_record_id=source_record_id,
        observed_or_issued_at=observed_or_issued_at,
        truth_model_version=SETTLEMENT_AUTHORITY_VERSION,
        revision_of=revision_of,
    )
    settlement_id = _stable_id("authoritative-settlement", truth_id, event_ticker, rules_hash)
    return AuthoritativeSettlement(
        settlement_id=settlement_id,
        event_ticker=event_ticker,
        rules_hash=rules_hash,
        rule_source_name=rule_source_name,
        settlement_source_name=settlement_source_name,
        authority=authority,
        truth=truth,
    )


def _validation_product(
    *,
    source: str,
    source_product_id: str,
    station_code: str,
    climate_day: date | None,
    reported_max_f: int | None,
    max_observed_at: datetime | None,
    issued_at: datetime,
    mercury_received_at: datetime,
    source_record_id: str,
    source_payload_sha256: str,
    lifecycle: ValidationLifecycle,
    authority: ValidationAuthority,
    parser_version: str,
    corrected: bool,
    revision_of: str | None,
    fail_closed_reason: str | None,
    metadata: Mapping[str, Any],
) -> ValidationProduct:
    validation_id = _stable_id(
        "validation-product",
        source,
        source_product_id,
        station_code,
        climate_day.isoformat() if climate_day else "",
        str(reported_max_f) if reported_max_f is not None else "",
        source_record_id,
        source_payload_sha256,
        lifecycle.value,
        parser_version,
        VALIDATION_MODEL_VERSION,
    )
    return ValidationProduct(
        validation_id=validation_id,
        source=source,
        source_product_id=source_product_id,
        station_code=station_code,
        climate_date=climate_day,
        reported_max_f=reported_max_f,
        max_observed_at=max_observed_at,
        issued_at=issued_at,
        mercury_received_at=mercury_received_at,
        source_record_id=source_record_id,
        source_payload_sha256=source_payload_sha256,
        lifecycle=lifecycle,
        authority=authority,
        parser_version=parser_version,
        corrected=corrected,
        revision_of=revision_of,
        fail_closed_reason=fail_closed_reason,
        metadata=metadata,
    )


def _infer_dsm_date(*, day: int, month: int, issued_at: datetime, timezone_name: str) -> date:
    issue_day = local_standard_time(issued_at, timezone_name).date()
    candidates: list[date] = []
    for year in (issue_day.year - 1, issue_day.year, issue_day.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        raise ValueError("invalid DSM date")
    # Product dates should be near issuance. The nearest valid date handles
    # Dec/Jan year rollover without inventing a year from the DD/MM token alone.
    return min(candidates, key=lambda candidate: (abs((candidate - issue_day).days), candidate > issue_day))


def _signed_int(token: str) -> int:
    text = token.strip().upper()
    if text.startswith("M"):
        text = "-" + text[1:]
    value = int(text)
    if value < -150 or value > 150:
        raise ValueError("temperature outside supported Fahrenheit range")
    return value


def _hhmm(token: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}", token):
        raise ValueError("invalid HHMM")
    hour, minute = int(token[:2]), int(token[2:])
    if hour > 23 or minute > 59:
        raise ValueError("invalid HHMM")
    return hour, minute


def _source_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts).encode("utf-8")
    return f"h4:{sha256(raw).hexdigest()}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _required_dt(value: Any) -> datetime:
    parsed = _dt(value)
    if parsed is None:
        raise ValueError("datetime is required")
    return parsed


def _dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))
