from __future__ import annotations

"""Pure source-agnostic bucket elimination for deterministic hard-state trading.

This module knows nothing about METAR, MADIS, Celsius, or weather parsers. It
consumes only a canonical ``HardClimateState`` plus the exact Kalshi event
snapshot used for the decision. Its only job is to answer which market YES
outcomes have become mathematically impossible.

A lower bound equal to a market cap does *not* eliminate the market. The hard
state must be strictly above the cap. Markets without a finite cap are valid
upper-tail outcomes and therefore cannot be killed by a lower-bound-only state.
Malformed or mismatched event metadata fails the whole event closed.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Mapping, Sequence

from hard_information_domain import BucketElimination, HardClimateState
from market_calendar import event_trade_date

ELIMINATION_MODEL_VERSION = "bucket-elimination-v1"


@dataclass(frozen=True)
class EventEliminationResult:
    event_ticker: str
    station_code: str
    climate_date: str
    hard_state_id: str
    transition_evidence_id: str
    event_rules_hash: str | None
    accepted: bool
    fail_closed_reason: str | None
    eliminations: tuple[BucketElimination, ...]
    elimination_model_version: str = ELIMINATION_MODEL_VERSION

    @property
    def eliminated(self) -> tuple[BucketElimination, ...]:
        return tuple(item for item in self.eliminations if item.eliminated)

    @property
    def dead_market_tickers(self) -> tuple[str, ...]:
        return tuple(item.market_ticker for item in self.eliminated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_ticker": self.event_ticker,
            "station_code": self.station_code,
            "climate_date": self.climate_date,
            "hard_state_id": self.hard_state_id,
            "transition_evidence_id": self.transition_evidence_id,
            "event_rules_hash": self.event_rules_hash,
            "accepted": self.accepted,
            "fail_closed_reason": self.fail_closed_reason,
            "elimination_model_version": self.elimination_model_version,
            "eliminations": [item.to_dict() for item in self.eliminations],
            "dead_market_tickers": list(self.dead_market_tickers),
        }


@dataclass(frozen=True)
class _Strike:
    ticker: str
    floor: Decimal | None
    cap: Decimal | None
    strike_type: str | None


def evaluate_event(event: Mapping[str, Any], hard_state: HardClimateState) -> EventEliminationResult:
    """Evaluate one exact event snapshot against a canonical hard state.

    Required event metadata:
    - ``event_ticker``: must encode the same climate date as the hard state;
    - ``station_code``: normalized by Mercury's series/station adapter and must
      exactly equal the state's station;
    - ``rules_hash``: immutable identity of the Kalshi event snapshot;
    - ``markets``: exact market strike metadata from that snapshot.

    The function is deterministic and has no database/network side effects.
    """
    ticker = str(event.get("event_ticker") or "")
    station = str(event.get("station_code") or "")
    rules_hash = str(event.get("rules_hash") or "") or None

    failure = _event_failure_reason(
        ticker=ticker,
        station=station,
        rules_hash=rules_hash,
        event=event,
        hard_state=hard_state,
    )
    if failure is not None:
        return _rejected(ticker, station, rules_hash, hard_state, failure)

    try:
        strikes = _parse_strikes(event.get("markets"))
    except ValueError as exc:
        return _rejected(ticker, station, rules_hash, hard_state, str(exc))

    lower_bound = Decimal(hard_state.proven_daily_high_min_f)
    eliminations: list[BucketElimination] = []
    for strike in strikes:
        if strike.cap is None:
            eliminated = False
            reason = "no_finite_upper_bound"
        else:
            eliminated = lower_bound > strike.cap
            reason = (
                "hard_lower_bound_strictly_above_market_cap"
                if eliminated else "hard_lower_bound_not_above_market_cap"
            )

        strike_rule = _strike_rule(strike)
        elimination_id = _stable_id(
            ELIMINATION_MODEL_VERSION,
            rules_hash,
            hard_state.state_id,
            hard_state.transition_evidence_id,
            ticker,
            strike.ticker,
            _fmt(strike.floor),
            _fmt(strike.cap),
            strike.strike_type or "",
            str(eliminated),
        )
        eliminations.append(BucketElimination(
            elimination_id=elimination_id,
            event_ticker=ticker,
            market_ticker=strike.ticker,
            station_code=hard_state.station_code,
            climate_date=hard_state.climate_date,
            hard_state_id=hard_state.state_id,
            hard_lower_bound_f=hard_state.proven_daily_high_min_f,
            strike_rule=strike_rule,
            eliminated=eliminated,
            elimination_model_version=ELIMINATION_MODEL_VERSION,
            reason=reason,
        ))

    return EventEliminationResult(
        event_ticker=ticker,
        station_code=station,
        climate_date=hard_state.climate_date.isoformat(),
        hard_state_id=hard_state.state_id,
        transition_evidence_id=hard_state.transition_evidence_id,
        event_rules_hash=rules_hash,
        accepted=True,
        fail_closed_reason=None,
        eliminations=tuple(eliminations),
    )


def _event_failure_reason(
    *,
    ticker: str,
    station: str,
    rules_hash: str | None,
    event: Mapping[str, Any],
    hard_state: HardClimateState,
) -> str | None:
    if not ticker:
        return "missing_event_ticker"
    trade_date = event_trade_date(ticker)
    if trade_date is None:
        return "unparseable_event_date"
    if trade_date != hard_state.climate_date:
        return "event_climate_date_mismatch"
    if not station:
        return "missing_event_station"
    if station != hard_state.station_code:
        return "event_station_mismatch"
    if not rules_hash:
        return "missing_event_rules_hash"
    markets = event.get("markets")
    if not isinstance(markets, Sequence) or isinstance(markets, (str, bytes)) or not markets:
        return "missing_event_markets"
    return None


def _parse_strikes(raw_markets: Any) -> tuple[_Strike, ...]:
    if not isinstance(raw_markets, Sequence) or isinstance(raw_markets, (str, bytes)):
        raise ValueError("invalid_event_markets")

    out: list[_Strike] = []
    seen: set[str] = set()
    for raw in raw_markets:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid_market_metadata")
        ticker = str(raw.get("ticker") or "")
        if not ticker:
            raise ValueError("missing_market_ticker")
        if ticker in seen:
            raise ValueError("duplicate_market_ticker")
        seen.add(ticker)

        floor_raw = raw.get("floor_strike")
        cap_raw = raw.get("cap_strike")
        floor = _bound(floor_raw, "invalid_floor_strike")
        cap = _bound(cap_raw, "invalid_cap_strike")
        if floor is None and cap is None:
            raise ValueError("market_missing_both_strike_bounds")
        if floor is not None and cap is not None and floor > cap:
            raise ValueError("market_floor_above_cap")

        strike_type_raw = raw.get("strike_type")
        strike_type = None if strike_type_raw is None else str(strike_type_raw)
        out.append(_Strike(ticker=ticker, floor=floor, cap=cap, strike_type=strike_type))
    return tuple(out)


def _bound(raw: Any, code: str) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(code) from None
    if not value.is_finite():
        raise ValueError(code)
    return value


def _strike_rule(strike: _Strike) -> str:
    if strike.floor is None:
        return f"cap_strike={_fmt(strike.cap)}"
    if strike.cap is None:
        return f"floor_strike={_fmt(strike.floor)};cap_strike=UNBOUNDED"
    return f"floor_strike={_fmt(strike.floor)};cap_strike={_fmt(strike.cap)}"


def _fmt(value: Decimal | None) -> str:
    if value is None:
        return "NONE"
    return format(value, "f")


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return f"elim:{sha256(raw).hexdigest()[:24]}"


def _rejected(
    ticker: str,
    station: str,
    rules_hash: str | None,
    hard_state: HardClimateState,
    reason: str,
) -> EventEliminationResult:
    return EventEliminationResult(
        event_ticker=ticker,
        station_code=station,
        climate_date=hard_state.climate_date.isoformat(),
        hard_state_id=hard_state.state_id,
        transition_evidence_id=hard_state.transition_evidence_id,
        event_rules_hash=rules_hash,
        accepted=False,
        fail_closed_reason=reason,
        eliminations=(),
    )
