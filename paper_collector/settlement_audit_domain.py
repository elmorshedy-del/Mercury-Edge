from __future__ import annotations

"""Pure canonical inputs for Step 4H-D settlement auditing."""

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from typing import Any, Mapping

from hard_information_domain import BucketElimination, HardClimateState
from market_calendar import event_trade_date

EXCHANGE_SETTLEMENT_MODEL_VERSION = "exchange-market-settlement-v1"
TRADE_PROOF_MODEL_VERSION = "benchmark-trade-proof-v1"


@dataclass(frozen=True, order=True)
class SettledMarketResult:
    market_ticker: str
    result: str

    def __post_init__(self) -> None:
        if not self.market_ticker.strip():
            raise ValueError("settled market ticker is required")
        if self.result not in {"yes", "no"}:
            raise ValueError("settled market result must be yes or no")

    def to_dict(self) -> dict[str, str]:
        return {"market_ticker": self.market_ticker, "result": self.result}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SettledMarketResult":
        return cls(market_ticker=str(value["market_ticker"]), result=str(value["result"]))


@dataclass(frozen=True)
class ExchangeMarketSettlement:
    exchange_settlement_id: str
    event_ticker: str
    station_code: str
    climate_date: date
    source_record_id: str
    source_payload_sha256: str
    rules_hash: str
    rule_source_name: str
    captured_at: datetime
    market_results: tuple[SettledMarketResult, ...]
    parser_version: str = EXCHANGE_SETTLEMENT_MODEL_VERSION

    def __post_init__(self) -> None:
        if event_trade_date(self.event_ticker) != self.climate_date:
            raise ValueError("exchange settlement event date does not match climate date")
        if not self.station_code.strip():
            raise ValueError("exchange settlement station is required")
        if not self.source_record_id.startswith("raw_source_journal:"):
            raise ValueError("exchange settlement requires immutable raw source id")
        if len(self.source_payload_sha256) != 64:
            raise ValueError("exchange settlement requires payload sha256")
        if len(self.rules_hash) != 64:
            raise ValueError("exchange settlement requires exact rules hash")
        if not self.market_results:
            raise ValueError("exchange settlement requires at least one resolved market")
        tickers = [item.market_ticker for item in self.market_results]
        if len(tickers) != len(set(tickers)):
            raise ValueError("duplicate market ticker in exchange settlement")
        if tuple(sorted(self.market_results)) != self.market_results:
            raise ValueError("exchange market results must be canonically sorted")

    def result_for(self, market_ticker: str) -> str | None:
        for item in self.market_results:
            if item.market_ticker == market_ticker:
                return item.result
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange_settlement_id": self.exchange_settlement_id,
            "event_ticker": self.event_ticker,
            "station_code": self.station_code,
            "climate_date": self.climate_date.isoformat(),
            "source_record_id": self.source_record_id,
            "source_payload_sha256": self.source_payload_sha256,
            "rules_hash": self.rules_hash,
            "rule_source_name": self.rule_source_name,
            "captured_at": self.captured_at.isoformat(),
            "market_results": [item.to_dict() for item in self.market_results],
            "parser_version": self.parser_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExchangeMarketSettlement":
        return cls(
            exchange_settlement_id=str(value["exchange_settlement_id"]),
            event_ticker=str(value["event_ticker"]),
            station_code=str(value["station_code"]),
            climate_date=date.fromisoformat(str(value["climate_date"])),
            source_record_id=str(value["source_record_id"]),
            source_payload_sha256=str(value["source_payload_sha256"]),
            rules_hash=str(value["rules_hash"]),
            rule_source_name=str(value["rule_source_name"]),
            captured_at=datetime.fromisoformat(str(value["captured_at"]).replace("Z", "+00:00")),
            market_results=tuple(
                SettledMarketResult.from_dict(item)
                for item in value.get("market_results", [])
            ),
            parser_version=str(value.get("parser_version", EXCHANGE_SETTLEMENT_MODEL_VERSION)),
        )


@dataclass(frozen=True)
class BenchmarkTradeProof:
    session_id: str
    order_id: int
    outcome_side: str
    event_rules_hash: str
    hard_state: HardClimateState
    elimination: BucketElimination
    proof_model_version: str = TRADE_PROOF_MODEL_VERSION

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("benchmark trade proof session is required")
        if self.order_id <= 0:
            raise ValueError("benchmark trade proof order id must be positive")
        if self.outcome_side != "no":
            raise ValueError("hard-edge benchmark settlement audit expects NO side")
        if len(self.event_rules_hash) != 64:
            raise ValueError("benchmark trade proof requires exact event rules hash")
        if not self.elimination.eliminated:
            raise ValueError("benchmark trade proof requires an eliminated market")
        if self.elimination.hard_state_id != self.hard_state.state_id:
            raise ValueError("elimination hard-state identity mismatch")
        if self.elimination.station_code != self.hard_state.station_code:
            raise ValueError("elimination hard-state station mismatch")
        if self.elimination.climate_date != self.hard_state.climate_date:
            raise ValueError("elimination hard-state climate-date mismatch")
        if self.elimination.hard_lower_bound_f != self.hard_state.proven_daily_high_min_f:
            raise ValueError("elimination hard-state bound mismatch")

    @property
    def event_ticker(self) -> str:
        return self.elimination.event_ticker

    @property
    def market_ticker(self) -> str:
        return self.elimination.market_ticker

    @property
    def station_code(self) -> str:
        return self.elimination.station_code

    @property
    def climate_date(self) -> date:
        return self.elimination.climate_date

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "order_id": self.order_id,
            "outcome_side": self.outcome_side,
            "event_rules_hash": self.event_rules_hash,
            "hard_state": self.hard_state.to_dict(),
            "elimination": self.elimination.to_dict(),
            "proof_model_version": self.proof_model_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BenchmarkTradeProof":
        return cls(
            session_id=str(value["session_id"]),
            order_id=int(value["order_id"]),
            outcome_side=str(value["outcome_side"]),
            event_rules_hash=str(value["event_rules_hash"]),
            hard_state=HardClimateState.from_dict(dict(value["hard_state"])),
            elimination=BucketElimination.from_dict(dict(value["elimination"])),
            proof_model_version=str(value.get("proof_model_version", TRADE_PROOF_MODEL_VERSION)),
        )


def build_exchange_market_settlement(
    *,
    event_ticker: str,
    station_code: str,
    climate_date: date,
    source_record_id: str,
    source_payload_sha256: str,
    rules_hash: str,
    rule_source_name: str,
    captured_at: datetime,
    market_results: tuple[tuple[str, str], ...],
    parser_version: str = EXCHANGE_SETTLEMENT_MODEL_VERSION,
) -> ExchangeMarketSettlement:
    canonical_results = tuple(
        sorted(
            (SettledMarketResult(str(ticker), str(result).lower()) for ticker, result in market_results),
            key=lambda item: item.market_ticker,
        )
    )
    identity = "|".join((
        parser_version,
        event_ticker,
        station_code.upper(),
        climate_date.isoformat(),
        source_record_id,
        source_payload_sha256,
        rules_hash,
        rule_source_name,
        captured_at.isoformat(),
        ";".join(f"{item.market_ticker}:{item.result}" for item in canonical_results),
    )).encode("utf-8")
    exchange_id = f"exchange-settlement:{sha256(identity).hexdigest()[:40]}"
    return ExchangeMarketSettlement(
        exchange_settlement_id=exchange_id,
        event_ticker=event_ticker,
        station_code=station_code.upper(),
        climate_date=climate_date,
        source_record_id=source_record_id,
        source_payload_sha256=source_payload_sha256,
        rules_hash=rules_hash,
        rule_source_name=rule_source_name,
        captured_at=captured_at,
        market_results=canonical_results,
        parser_version=parser_version,
    )
