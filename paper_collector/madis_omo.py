from __future__ import annotations

"""MADIS OMO 1-minute-cadence source contract — research only for Step 4G.

The important semantic distinction is that a MADIS ``1-minute ASOS`` record is
an OMO *issued each minute*; its air-temperature field is not one of the hidden
1-minute sensor averages that Mercury should average again.

The NWS ASOS User's Guide documents the temperature pipeline as:

sensor samples -> 1-minute average -> running 5-minute average -> whole °F ->
nearest 0.1 °C -> OMO/METAR temperature.

Thus the OMO ``T`` field represents the current ASOS running five-minute
climate temperature reported on a one-minute cadence. MADIS stores ``T`` as
Kelvin. Mercury still must not turn that Kelvin number directly into settlement
proof until the MADIS storage/encoding representation is decoded through a
versioned inverse lattice.

This module therefore only owns transport-decoded MADIS fields and clocks. It
never grants benchmark authority and never performs a second rolling average.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping, Protocol, runtime_checkable

from hard_information_domain import (
    EvidenceTrust,
    EvidenceType,
    IntegrityStatus,
    NormalizedObservation,
    RawSourceRecord,
    SettlementEvidence,
    SourceClocks,
)
from market_calendar import CLIMATE_CALENDAR_VERSION, climate_date

MADIS_OMO_SOURCE = "MADIS_OMO"
MADIS_OMO_TEMPERATURE_VARIABLE = "T"
MADIS_OMO_TEMPERATURE_UNIT = "K"
# The enum name reflects the source cadence/dataset name. It does NOT mean the
# temperature value is a raw one-minute input to the ASOS five-minute average.
MADIS_OMO_OBSERVATION_TYPE = EvidenceType.MADIS_OMO_1MIN.value
MADIS_OMO_ADAPTER_VERSION = "madis-omo-adapter-contract-v2"
MADIS_OMO_RESEARCH_EVIDENCE_VERSION = "madis-omo-wire-research-v2"
OMO_TEMPERATURE_SEMANTICS = "asos_running_5min_average_reported_each_minute"


class MadisMinuteStatus(str, Enum):
    ACCEPTED_RESEARCH = "accepted_research"
    INCOMPLETE = "incomplete"
    QC_REJECTED = "qc_rejected"
    CLOCK_SKEW = "clock_skew"
    INVALID_UNIT = "invalid_unit"
    INVALID_VARIABLE = "invalid_variable"
    INVALID_SOURCE = "invalid_source"


@dataclass(frozen=True)
class MadisOmoMinute:
    """One OMO record on the one-minute cadence with immutable provenance."""

    minute_id: str
    raw_record_id: str
    station_code: str
    climate_date: date
    observed_at: datetime
    source_published_at: datetime | None
    first_fetchable_at: datetime | None
    ldm_received_at: datetime
    mercury_interpreted_at: datetime
    temperature: Decimal | None
    temperature_unit: str | None
    upstream_variable: str | None
    temperature_sensor_status: int | None
    qc_status: str | None
    sequence_key: str | None
    status: MadisMinuteStatus
    parser_version: str
    calendar_version: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def research_usable(self) -> bool:
        return self.status is MadisMinuteStatus.ACCEPTED_RESEARCH and self.temperature is not None

    @property
    def sensor_status_verified(self) -> bool:
        # MADIS note 35: TSS=0 means sensor operating/data available. A missing
        # TSS is preserved for exploratory research but does not qualify for the
        # direct five-minute climate-state evidence adapter.
        return self.temperature_sensor_status == 0

    @property
    def latency_ms_from_observation(self) -> int:
        return int((self.ldm_received_at - self.observed_at).total_seconds() * 1000)

    def to_normalized_observation(self) -> NormalizedObservation | None:
        if not self.research_usable or self.temperature is None or self.temperature_unit is None:
            return None
        return NormalizedObservation(
            observation_id=self.minute_id,
            source_record_id=self.raw_record_id,
            station_code=self.station_code,
            climate_date=self.climate_date,
            observation_type=MADIS_OMO_OBSERVATION_TYPE,
            value=self.temperature,
            unit=self.temperature_unit,
            clocks=SourceClocks(
                observed_at=self.observed_at,
                source_published_at=self.source_published_at,
                first_fetchable_at=self.first_fetchable_at,
                mercury_received_at=self.ldm_received_at,
                mercury_interpreted_at=self.mercury_interpreted_at,
            ),
            parser_version=self.parser_version,
            calendar_version=self.calendar_version,
            metadata={
                **dict(self.metadata),
                "source": MADIS_OMO_SOURCE,
                "upstream_variable": self.upstream_variable,
                "temperature_sensor_status": self.temperature_sensor_status,
                "sensor_status_verified": self.sensor_status_verified,
                "qc_status": self.qc_status,
                "sequence_key": self.sequence_key,
                "madis_minute_status": self.status.value,
                "omo_temperature_semantics": OMO_TEMPERATURE_SEMANTICS,
                "benchmark_eligible": False,
                "transport": "ldm",
            },
        )

    def to_research_evidence(self) -> SettlementEvidence | None:
        """Expose the raw OMO wire value without pretending Kelvin is canonical °F.

        The OMO temperature already represents the ASOS running five-minute
        state, but MADIS stores it in Kelvin. Until the Kelvin representation is
        passed through the versioned inverse-lattice mapper, this raw item has no
        canonical Fahrenheit bound.
        """
        observation = self.to_normalized_observation()
        if observation is None:
            return None
        evidence_id = _stable_id(
            "madis-omo-wire-research",
            self.minute_id,
            MADIS_OMO_RESEARCH_EVIDENCE_VERSION,
        )
        return SettlementEvidence(
            evidence_id=evidence_id,
            evidence_type=EvidenceType.MADIS_OMO_1MIN,
            station_code=self.station_code,
            climate_date=self.climate_date,
            source_record_ids=(self.raw_record_id,),
            proven_min_f=None,
            proven_max_f=None,
            integrity_status=IntegrityStatus.CANONICAL,
            trust=EvidenceTrust.RESEARCH_ONLY,
            clocks=observation.clocks,
            parser_version=self.parser_version,
            evidence_model_version=MADIS_OMO_RESEARCH_EVIDENCE_VERSION,
            calendar_version=self.calendar_version,
            raw_identifier=self.upstream_variable,
            possible_canonical_f=(),
            fail_closed_reason="madis_omo_kelvin_requires_versioned_inverse_lattice",
            metadata=observation.metadata,
        )


@dataclass(frozen=True)
class MadisAdapterResult:
    minute: MadisOmoMinute
    fail_closed_reason: str | None = None

    @property
    def accepted_for_research(self) -> bool:
        return self.minute.research_usable and self.fail_closed_reason is None

    @property
    def benchmark_eligible(self) -> bool:
        return False


@runtime_checkable
class MadisOmoSourceAdapter(Protocol):
    """Interface a future MADIS archive/LDM binary parser must implement."""

    adapter_version: str

    def parse_minute(
        self,
        *,
        raw_record: RawSourceRecord,
        station_timezone: str,
        mercury_interpreted_at: datetime,
        fields: Mapping[str, Any],
    ) -> MadisAdapterResult:
        ...


class ContractMadisOmoAdapter:
    """Deterministic contract over transport-decoded official MADIS fields.

    A future binary/netCDF/LDM reader maps documented MADIS ``T``/``TSS`` fields
    into this contract. Wire decoding stays outside climate-state interpretation.
    """

    adapter_version = MADIS_OMO_ADAPTER_VERSION

    def parse_minute(
        self,
        *,
        raw_record: RawSourceRecord,
        station_timezone: str,
        mercury_interpreted_at: datetime,
        fields: Mapping[str, Any],
    ) -> MadisAdapterResult:
        interpreted = _aware(mercury_interpreted_at)
        received = _aware(raw_record.clocks.mercury_received_at)
        observed = _optional_datetime(fields.get("observed_at")) or _aware(raw_record.clocks.observed_at)
        station = str(fields.get("station_code") or raw_record.station_code or "").upper().strip()
        temperature = _optional_decimal(fields.get("temperature"))
        unit = str(fields.get("temperature_unit") or "").strip() or None
        variable = str(fields.get("upstream_variable") or "").strip() or None
        tss = _optional_int(fields.get("temperature_sensor_status"))
        qc_status = str(fields.get("qc_status") or "").strip() or None

        status = MadisMinuteStatus.ACCEPTED_RESEARCH
        reason: str | None = None

        if raw_record.source != MADIS_OMO_SOURCE:
            status = MadisMinuteStatus.INVALID_SOURCE
            reason = "raw_source_is_not_madis_omo"
        elif not station:
            status = MadisMinuteStatus.INCOMPLETE
            reason = "missing_station_code"
        elif variable != MADIS_OMO_TEMPERATURE_VARIABLE:
            status = MadisMinuteStatus.INVALID_VARIABLE
            reason = "unexpected_madis_temperature_variable"
        elif temperature is None:
            status = MadisMinuteStatus.INCOMPLETE
            reason = "missing_temperature"
        elif unit != MADIS_OMO_TEMPERATURE_UNIT:
            status = MadisMinuteStatus.INVALID_UNIT
            reason = "madis_temperature_unit_must_be_kelvin"
        elif tss is not None and tss != 0:
            status = MadisMinuteStatus.QC_REJECTED
            reason = "temperature_sensor_status_not_operating"
        elif not _qc_allows_research(qc_status):
            status = MadisMinuteStatus.QC_REJECTED
            reason = "upstream_qc_not_accepted"
        elif received < observed:
            status = MadisMinuteStatus.CLOCK_SKEW
            reason = "ldm_receipt_precedes_observation_clock"
        elif interpreted < received:
            status = MadisMinuteStatus.CLOCK_SKEW
            reason = "interpretation_precedes_ldm_receipt"

        day = climate_date(observed, station_timezone)
        minute_id = _stable_id(
            "madis-omo-minute",
            raw_record.record_id,
            station,
            observed.isoformat(),
            variable or "unknown-variable",
            self.adapter_version,
            CLIMATE_CALENDAR_VERSION,
        )
        minute = MadisOmoMinute(
            minute_id=minute_id,
            raw_record_id=raw_record.record_id,
            station_code=station,
            climate_date=day,
            observed_at=observed,
            source_published_at=raw_record.clocks.source_published_at,
            first_fetchable_at=raw_record.clocks.first_fetchable_at,
            ldm_received_at=received,
            mercury_interpreted_at=interpreted,
            temperature=temperature,
            temperature_unit=unit,
            upstream_variable=variable,
            temperature_sensor_status=tss,
            qc_status=qc_status,
            sequence_key=raw_record.sequence_key,
            status=status,
            parser_version=self.adapter_version,
            calendar_version=CLIMATE_CALENDAR_VERSION,
            metadata={
                "raw_payload_hash": raw_record.payload_hash,
                "raw_source": raw_record.source,
                "raw_transport": raw_record.transport,
                "official_madis_temperature_variable": MADIS_OMO_TEMPERATURE_VARIABLE,
                "official_madis_temperature_unit": MADIS_OMO_TEMPERATURE_UNIT,
                "omo_temperature_semantics": OMO_TEMPERATURE_SEMANTICS,
                "source_release_to_ldm_ms": _latency_ms(raw_record.clocks.source_published_at, received),
                "first_fetchable_to_ldm_ms": _latency_ms(raw_record.clocks.first_fetchable_at, received),
                "observation_to_ldm_ms": _latency_ms(observed, received),
                "ldm_to_interpretation_ms": _latency_ms(received, interpreted),
            },
        )
        return MadisAdapterResult(minute=minute, fail_closed_reason=reason)


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}:{sha256(payload).hexdigest()[:24]}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _qc_allows_research(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip().lower() not in {"bad", "reject", "rejected", "failed", "invalid"}


def _latency_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return int((_aware(end) - _aware(start)).total_seconds() * 1000)
