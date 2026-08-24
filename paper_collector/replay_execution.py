from __future__ import annotations

"""Step 4J-C exact causal L2 and benchmark portfolio replay.

The source journal is read-only. This module reconstructs the exchange book from
raw WebSocket messages no later than the configured simulated arrival, applies
the current dead-NO execution math, and evolves a private replay portfolio.
There is no candle/midpoint/proxy fallback.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import psycopg

import dead_no_execution as dne
from raw_journal import canonical_json_bytes
from replay_domain import ReplayManifest
from replay_hard_state import ReplayHardStateResult, ReplayTransitionElimination, UnsupportedReplayVersion

EXECUTION_MODEL_VERSION = "canonical-dead-no-paper-v1"
REPLAY_EXECUTION_VERSION = "replay-execution-v1"
ONE = Decimal("1")


@dataclass(frozen=True)
class ReplayMarketRow:
    row_id: int
    channel: str
    sid: int | None
    seq: int | None
    market_ticker: str | None
    received_at: datetime
    received_epoch_ms: int
    received_epoch_ns: int
    raw_text: str
    payload_sha256: str
    connection_id: str | None
    prev_chain_hash: str | None
    chain_hash: str | None
    price_mode: str | None = None

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is None:
            raise ValueError("market receipt must be timezone-aware")


@dataclass(frozen=True)
class ReplayBook:
    market_ticker: str
    connection_id: str
    snapshot_id: int
    snapshot_received_ms: int
    last_seq: int | None
    yes_bids: tuple[tuple[Decimal, Decimal], ...]
    no_bids: tuple[tuple[Decimal, Decimal], ...]

    @property
    def no_asks(self) -> tuple[tuple[Decimal, Decimal], ...]:
        return tuple(sorted((ONE - price, qty) for price, qty in self.yes_bids if qty > 0))


@dataclass(frozen=True)
class ReplayExecutionConfig:
    starting_bankroll: Decimal
    starting_cash: Decimal
    execution_latency_ms: int = 100
    fee_multiplier: Decimal = Decimal("1")
    max_no_price: Decimal = Decimal("1.00")
    reserve_pct: Decimal = Decimal("0")
    max_trade_pct: Decimal = Decimal("1")
    max_event_pct: Decimal = Decimal("1")
    max_region_pct: Decimal = Decimal("1")
    max_daily_deployed_pct: Decimal = Decimal("1")
    allocation: str = "best_edge_first"
    risk_multiplier: Decimal = Decimal("1")
    region: str = "default"
    portfolio_day_timezone: str = "America/New_York"
    prior_event_deployed: Decimal = Decimal("0")
    prior_region_deployed: Decimal = Decimal("0")
    prior_daily_deployed: Decimal = Decimal("0")
    benchmark_label: str = "benchmark"

    def __post_init__(self) -> None:
        decimals = (
            self.starting_bankroll, self.starting_cash, self.fee_multiplier,
            self.max_no_price, self.reserve_pct, self.max_trade_pct,
            self.max_event_pct, self.max_region_pct, self.max_daily_deployed_pct,
            self.risk_multiplier, self.prior_event_deployed,
            self.prior_region_deployed, self.prior_daily_deployed,
        )
        if any(Decimal(v) < 0 for v in decimals):
            raise ValueError("execution configuration cannot contain negative values")
        if self.starting_cash > self.starting_bankroll:
            raise ValueError("starting_cash cannot exceed starting_bankroll")
        if self.execution_latency_ms < 0:
            raise ValueError("execution_latency_ms must be non-negative")
        if self.max_no_price > ONE or self.risk_multiplier > ONE:
            raise ValueError("max_no_price/risk_multiplier cannot exceed 1")
        if self.benchmark_label != "benchmark":
            raise ValueError("benchmark replay config cannot be relabeled as research")

    def to_dict(self) -> dict[str, Any]:
        return {
            "starting_bankroll": str(self.starting_bankroll),
            "starting_cash": str(self.starting_cash),
            "execution_latency_ms": self.execution_latency_ms,
            "fee_multiplier": str(self.fee_multiplier),
            "max_no_price": str(self.max_no_price),
            "reserve_pct": str(self.reserve_pct),
            "max_trade_pct": str(self.max_trade_pct),
            "max_event_pct": str(self.max_event_pct),
            "max_region_pct": str(self.max_region_pct),
            "max_daily_deployed_pct": str(self.max_daily_deployed_pct),
            "allocation": self.allocation,
            "risk_multiplier": str(self.risk_multiplier),
            "region": self.region,
            "portfolio_day_timezone": self.portfolio_day_timezone,
            "prior_event_deployed": str(self.prior_event_deployed),
            "prior_region_deployed": str(self.prior_region_deployed),
            "prior_daily_deployed": str(self.prior_daily_deployed),
            "benchmark_label": self.benchmark_label,
        }

    @property
    def config_sha256(self) -> str:
        return sha256(canonical_json_bytes(self.to_dict())).hexdigest()


@dataclass(frozen=True)
class ReplayPosition:
    market_ticker: str
    qty: Decimal
    gross_cost: Decimal
    fees: Decimal

    def to_dict(self) -> dict[str, str]:
        return {
            "market_ticker": self.market_ticker,
            "qty": str(self.qty),
            "gross_cost": str(self.gross_cost),
            "fees": str(self.fees),
        }


@dataclass(frozen=True)
class ReplayDecision:
    decision_id: str
    state_id: str
    elimination_id: str | None
    event_ticker: str
    market_ticker: str
    decision_at: datetime
    simulated_arrival_at: datetime
    decision: str
    reason: str
    requested_budget: Decimal
    filled_qty: Decimal = Decimal("0")
    gross_cost: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    guaranteed_profit: Decimal = Decimal("0")
    guaranteed_roi: Decimal = Decimal("0")
    fills: tuple[tuple[Decimal, Decimal], ...] = ()
    connection_id: str | None = None
    snapshot_id: int | None = None
    book_seq: int | None = None
    execution_model_version: str = EXECUTION_MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "state_id": self.state_id,
            "elimination_id": self.elimination_id,
            "event_ticker": self.event_ticker,
            "market_ticker": self.market_ticker,
            "decision_at": self.decision_at.isoformat(),
            "simulated_arrival_at": self.simulated_arrival_at.isoformat(),
            "decision": self.decision,
            "reason": self.reason,
            "requested_budget": str(self.requested_budget),
            "filled_qty": str(self.filled_qty),
            "gross_cost": str(self.gross_cost),
            "fee": str(self.fee),
            "total_cost": str(self.total_cost),
            "guaranteed_profit": str(self.guaranteed_profit),
            "guaranteed_roi": str(self.guaranteed_roi),
            "fills": [[str(p), str(q)] for p, q in self.fills],
            "connection_id": self.connection_id,
            "snapshot_id": self.snapshot_id,
            "book_seq": self.book_seq,
            "execution_model_version": self.execution_model_version,
        }


@dataclass(frozen=True)
class ReplayExecutionResult:
    manifest_id: str
    source_input_sha256: str
    hard_state_output_sha256: str
    execution_config_sha256: str
    decisions: tuple[ReplayDecision, ...]
    ending_cash: Decimal
    positions: tuple[ReplayPosition, ...]
    event_deployed: Decimal
    region_deployed: Decimal
    daily_deployed: Decimal
    benchmark_pnl_basis: str = "unsettled_cost_basis_only"
    replay_execution_version: str = REPLAY_EXECUTION_VERSION

    @property
    def output_sha256(self) -> str:
        return sha256(canonical_json_bytes(self.to_dict(include_hash=False))).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {
            "manifest_id": self.manifest_id,
            "source_input_sha256": self.source_input_sha256,
            "hard_state_output_sha256": self.hard_state_output_sha256,
            "execution_config_sha256": self.execution_config_sha256,
            "decisions": [item.to_dict() for item in self.decisions],
            "ending_cash": str(self.ending_cash),
            "positions": [item.to_dict() for item in self.positions],
            "event_deployed": str(self.event_deployed),
            "region_deployed": str(self.region_deployed),
            "daily_deployed": str(self.daily_deployed),
            "benchmark_pnl_basis": self.benchmark_pnl_basis,
            "replay_execution_version": self.replay_execution_version,
        }
        if include_hash:
            payload["output_sha256"] = self.output_sha256
        return payload


@dataclass(frozen=True)
class ReplayCounterfactualResult:
    """Research-only output; never included in benchmark cash/P&L state."""
    label: str
    source_input_sha256: str
    research_payload: Mapping[str, Any]


@dataclass
class _Portfolio:
    start: Decimal
    cash: Decimal
    event_used: Decimal
    region_used: Decimal
    day_used: Decimal
    positions: dict[str, ReplayPosition] = field(default_factory=dict)


def assert_supported_execution(manifest: ReplayManifest) -> None:
    if manifest.versions.execution_version != EXECUTION_MODEL_VERSION:
        raise UnsupportedReplayVersion(
            f"UNSUPPORTED_VERSION: execution_version={manifest.versions.execution_version!r}"
        )


def audit_chain_hash(prev: str | None, connection_id: str, received_epoch_ns: int, raw_sha: str) -> str:
    material = f"{prev or ''}|{connection_id}|{received_epoch_ns}|{raw_sha}".encode()
    return sha256(material).hexdigest()


def reconstruct_book(
    rows: Sequence[ReplayMarketRow],
    *,
    market_ticker: str,
    arrival_ms: int,
) -> tuple[ReplayBook | None, str | None]:
    """Rebuild exact L2 no later than arrival and fail closed on integrity errors."""
    causal = sorted(
        (row for row in rows if row.received_epoch_ms <= arrival_ms),
        key=lambda row: (row.row_id, row.received_epoch_ns),
    )
    if not causal:
        return None, "NO_VALID_L2_AT_SIMULATED_ARRIVAL"

    invalid_connections: set[str] = set()
    last_chain: dict[str, str] = {}
    last_seq: dict[tuple[str, int], int] = {}
    snapshot_seen: set[tuple[str, str]] = set()
    books: dict[tuple[str, str], dict[str, Any]] = {}
    snapshot_candidates: list[tuple[int, int, str]] = []

    for row in causal:
        actual_sha = sha256(row.raw_text.encode()).hexdigest()
        conn_key = row.connection_id or "legacy"
        if actual_sha != row.payload_sha256:
            invalid_connections.add(conn_key)
            continue
        if row.connection_id:
            expected_prev = last_chain.get(row.connection_id)
            if (row.prev_chain_hash or None) != (expected_prev or None):
                invalid_connections.add(conn_key)
            expected_chain = audit_chain_hash(
                row.prev_chain_hash, row.connection_id, row.received_epoch_ns, row.payload_sha256
            )
            if row.chain_hash != expected_chain:
                invalid_connections.add(conn_key)
            if row.chain_hash:
                last_chain[row.connection_id] = row.chain_hash
        elif row.channel in ("orderbook_snapshot", "orderbook_delta"):
            invalid_connections.add(conn_key)

        try:
            data = json.loads(row.raw_text)
        except Exception:
            invalid_connections.add(conn_key)
            continue
        if row.channel not in ("orderbook_snapshot", "orderbook_delta"):
            continue

        if row.sid is None or row.seq is None:
            invalid_connections.add(conn_key)
        else:
            key = (conn_key, int(row.sid))
            previous = last_seq.get(key)
            if previous is not None and int(row.seq) != previous + 1:
                invalid_connections.add(conn_key)
            last_seq[key] = int(row.seq)

        msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
        ticker = str(msg.get("market_ticker") or row.market_ticker or "")
        if not ticker:
            invalid_connections.add(conn_key)
            continue
        book_key = (conn_key, ticker)

        try:
            if row.channel == "orderbook_snapshot":
                yes = _snapshot_side(msg.get("yes_dollars_fp") or [], "yes", row.price_mode)
                no = _snapshot_side(msg.get("no_dollars_fp") or [], "no", row.price_mode)
                _validate_binary_book(yes, no)
                books[book_key] = {
                    "yes": yes,
                    "no": no,
                    "snapshot_id": row.row_id,
                    "snapshot_received_ms": row.received_epoch_ms,
                    "last_seq": int(row.seq) if row.seq is not None else None,
                }
                snapshot_seen.add(book_key)
                if ticker == market_ticker:
                    snapshot_candidates.append((row.received_epoch_ms, row.row_id, conn_key))
            else:
                if book_key not in snapshot_seen or book_key not in books:
                    invalid_connections.add(conn_key)
                    continue
                side_name = str(msg.get("side") or "")
                if side_name not in ("yes", "no"):
                    invalid_connections.add(conn_key)
                    continue
                price = _native_price(side_name, str(msg.get("price_dollars")), row.price_mode)
                delta = Decimal(str(msg.get("delta_fp")))
                side = books[book_key][side_name]
                next_qty = side.get(price, Decimal(0)) + delta
                if next_qty < 0:
                    invalid_connections.add(conn_key)
                    continue
                if next_qty == 0:
                    side.pop(price, None)
                else:
                    side[price] = next_qty
                _validate_binary_book(books[book_key]["yes"], books[book_key]["no"])
                books[book_key]["last_seq"] = int(row.seq) if row.seq is not None else books[book_key]["last_seq"]
        except Exception:
            invalid_connections.add(conn_key)

    usable = [item for item in snapshot_candidates if item[2] not in invalid_connections]
    if not usable:
        reason = "L2_CONNECTION_INTEGRITY_FAILURE" if snapshot_candidates else "NO_VALID_L2_AT_SIMULATED_ARRIVAL"
        return None, reason
    _, _, conn_key = max(usable)
    state = books.get((conn_key, market_ticker))
    if not state:
        return None, "NO_VALID_L2_AT_SIMULATED_ARRIVAL"
    return ReplayBook(
        market_ticker=market_ticker,
        connection_id=conn_key,
        snapshot_id=int(state["snapshot_id"]),
        snapshot_received_ms=int(state["snapshot_received_ms"]),
        last_seq=state["last_seq"],
        yes_bids=tuple(sorted(state["yes"].items())),
        no_bids=tuple(sorted(state["no"].items())),
    ), None


def execute_replay(
    *,
    manifest: ReplayManifest,
    hard_state: ReplayHardStateResult,
    market_rows: Sequence[ReplayMarketRow],
    config: ReplayExecutionConfig,
) -> ReplayExecutionResult:
    assert_supported_execution(manifest)
    portfolio = _Portfolio(
        start=config.starting_bankroll,
        cash=config.starting_cash,
        event_used=config.prior_event_deployed,
        region_used=config.prior_region_deployed,
        day_used=config.prior_daily_deployed,
    )
    decisions: list[ReplayDecision] = []

    for elimination in sorted(hard_state.eliminations, key=lambda item: (item.known_at, item.state_id)):
        if not elimination.accepted or not elimination.elimination_payload:
            continue
        payload = elimination.elimination_payload
        event_ticker = str(payload.get("event_ticker") or "")
        eliminated = [item for item in payload.get("eliminations", []) if isinstance(item, Mapping) and item.get("eliminated") is True]
        evaluated: list[tuple[Mapping[str, Any], ReplayBook, tuple[tuple[Decimal, Decimal], ...], dne.DeadNoPlan, Decimal]] = []

        for item in eliminated:
            market = str(item.get("market_ticker") or "")
            arrival_ms = int(elimination.known_at.timestamp() * 1000) + config.execution_latency_ms
            arrival_at = datetime.fromtimestamp(arrival_ms / 1000, tz=timezone.utc)
            budget = _mode_budget(portfolio, config)
            if budget <= 0:
                decisions.append(_decision(
                    elimination, item, event_ticker, market, arrival_at,
                    "skip", "PORTFOLIO_CAP_REACHED", Decimal(0), config,
                ))
                continue
            book, book_reason = reconstruct_book(market_rows, market_ticker=market, arrival_ms=arrival_ms)
            if book is None:
                decisions.append(_decision(
                    elimination, item, event_ticker, market, arrival_at,
                    "blocked", book_reason or "NO_VALID_L2_AT_SIMULATED_ARRIVAL", budget, config,
                ))
                continue
            asks = tuple((price, qty) for price, qty in book.no_asks if price <= config.max_no_price and qty > 0)
            if not asks:
                decisions.append(_decision(
                    elimination, item, event_ticker, market, arrival_at,
                    "skip", "NO_EXECUTABLE_NO_ASK_WITHIN_GUARD", budget, config, book=book,
                ))
                continue
            plan = dne.plan_dead_no(
                asks,
                budget=budget,
                fee_multiplier=config.fee_multiplier,
                max_price=config.max_no_price,
            )
            if plan is None:
                decisions.append(_decision(
                    elimination, item, event_ticker, market, arrival_at,
                    "skip", "NO_POSITIVE_GUARANTEED_RETURN_AFTER_FEES", budget, config, book=book,
                ))
                continue
            depth = sum((price * qty for price, qty in asks), Decimal(0))
            evaluated.append((item, book, asks, plan, depth))

        if not evaluated:
            continue
        evaluated = _rank(evaluated, config.allocation)
        allocations = _allocations(evaluated, portfolio, config)
        for evaluated_item, allowed in zip(evaluated, allocations):
            item, book, asks, _, _ = evaluated_item
            market = str(item.get("market_ticker") or "")
            arrival_ms = int(elimination.known_at.timestamp() * 1000) + config.execution_latency_ms
            arrival_at = datetime.fromtimestamp(arrival_ms / 1000, tz=timezone.utc)
            if allowed <= 0:
                decisions.append(_decision(
                    elimination, item, event_ticker, market, arrival_at,
                    "skip", "PORTFOLIO_CAP_REACHED", Decimal(0), config, book=book,
                ))
                continue
            plan = dne.plan_dead_no(
                asks,
                budget=allowed,
                fee_multiplier=config.fee_multiplier,
                max_price=config.max_no_price,
            )
            if plan is None:
                decisions.append(_decision(
                    elimination, item, event_ticker, market, arrival_at,
                    "skip", "NO_POSITIVE_GUARANTEED_RETURN_AFTER_FEES", allowed, config, book=book,
                ))
                continue
            decision = _decision(
                elimination, item, event_ticker, market, arrival_at,
                "trade", "EXECUTABLE_DEAD_NO_GUARANTEED", allowed, config,
                book=book, plan=plan,
            )
            decisions.append(decision)
            portfolio.cash -= plan.total_cost
            portfolio.event_used += plan.total_cost
            portfolio.region_used += plan.total_cost
            portfolio.day_used += plan.total_cost
            prior = portfolio.positions.get(market)
            if prior is None:
                portfolio.positions[market] = ReplayPosition(market, plan.filled_qty, plan.gross_cost, plan.fee)
            else:
                portfolio.positions[market] = ReplayPosition(
                    market,
                    prior.qty + plan.filled_qty,
                    prior.gross_cost + plan.gross_cost,
                    prior.fees + plan.fee,
                )

    return ReplayExecutionResult(
        manifest_id=manifest.manifest_id,
        source_input_sha256=manifest.source_input_sha256,
        hard_state_output_sha256=hard_state.output_sha256,
        execution_config_sha256=config.config_sha256,
        decisions=tuple(decisions),
        ending_cash=portfolio.cash,
        positions=tuple(portfolio.positions[key] for key in sorted(portfolio.positions)),
        event_deployed=portfolio.event_used,
        region_deployed=portfolio.region_used,
        daily_deployed=portfolio.day_used,
    )


def load_market_rows(conn: psycopg.Connection[Any], *, session_id: str) -> tuple[ReplayMarketRow, ...]:
    rows = conn.execute(
        """
        SELECT id,channel,sid,seq,market_ticker,received_at,received_epoch_ms,
               received_epoch_ns,raw_text,payload_sha256,connection_id::text,
               prev_chain_hash,chain_hash,price_mode
          FROM market_data_journal
         WHERE session_id=%s
         ORDER BY id ASC
        """,
        (session_id,),
    ).fetchall()
    return tuple(ReplayMarketRow(
        row_id=int(row[0]), channel=str(row[1]), sid=int(row[2]) if row[2] is not None else None,
        seq=int(row[3]) if row[3] is not None else None,
        market_ticker=str(row[4]) if row[4] is not None else None,
        received_at=_aware(row[5]), received_epoch_ms=int(row[6]), received_epoch_ns=int(row[7]),
        raw_text=str(row[8]), payload_sha256=str(row[9]), connection_id=str(row[10]) if row[10] else None,
        prev_chain_hash=str(row[11]) if row[11] else None, chain_hash=str(row[12]) if row[12] else None,
        price_mode=str(row[13]) if row[13] else None,
    ) for row in rows)


def _rank(items: list[tuple[Mapping[str, Any], ReplayBook, tuple[tuple[Decimal, Decimal], ...], dne.DeadNoPlan, Decimal]], allocation: str):
    if allocation == "depth_first":
        return sorted(items, key=lambda item: (item[3].total_cost, item[3].guaranteed_roi, item[3].guaranteed_profit, str(item[0].get("market_ticker"))), reverse=True)
    return sorted(items, key=lambda item: (item[3].guaranteed_roi, item[3].guaranteed_profit, item[3].total_cost, str(item[0].get("market_ticker"))), reverse=True)


def _allocations(items, portfolio: _Portfolio, config: ReplayExecutionConfig) -> list[Decimal]:
    if config.allocation == "equal_risk":
        available = min((_mode_budget(portfolio, config) for _ in items), default=Decimal(0))
        each = available / Decimal(len(items)) if items else Decimal(0)
        return [min(each, _mode_budget(portfolio, config)) for _ in items]
    if config.allocation == "edge_weighted":
        available = _mode_budget(portfolio, config)
        total = sum((item[3].guaranteed_roi for item in items), Decimal(0))
        return [
            min(available * (item[3].guaranteed_roi / total if total > 0 else Decimal(1) / Decimal(len(items))), _mode_budget(portfolio, config))
            for item in items
        ]
    # Sequential modes recalculate budget after every fill in execute_replay.
    return [_mode_budget(portfolio, config) for _ in items]


def _mode_budget(portfolio: _Portfolio, config: ReplayExecutionConfig) -> Decimal:
    reserve_floor = portfolio.start * config.reserve_pct
    usable = max(Decimal(0), portfolio.cash - reserve_floor)
    raw = max(Decimal(0), min(
        usable,
        portfolio.start * config.max_trade_pct,
        max(Decimal(0), portfolio.start * config.max_event_pct - portfolio.event_used),
        max(Decimal(0), portfolio.start * config.max_region_pct - portfolio.region_used),
        max(Decimal(0), portfolio.start * config.max_daily_deployed_pct - portfolio.day_used),
    ))
    return raw * config.risk_multiplier


def _decision(elimination: ReplayTransitionElimination, item: Mapping[str, Any], event_ticker: str, market: str, arrival_at: datetime, decision: str, reason: str, budget: Decimal, config: ReplayExecutionConfig, *, book: ReplayBook | None = None, plan: dne.DeadNoPlan | None = None) -> ReplayDecision:
    elimination_id = str(item.get("elimination_id") or "") or None
    identity = {
        "manifest_state": elimination.state_id,
        "elimination_id": elimination_id,
        "event": event_ticker,
        "market": market,
        "arrival": arrival_at.isoformat(),
        "decision": decision,
        "reason": reason,
        "budget": str(budget),
        "config": config.config_sha256,
        "fills": [[str(p), str(q)] for p, q in (plan.fills if plan else ())],
    }
    decision_id = "replay-decision:" + sha256(canonical_json_bytes(identity)).hexdigest()[:24]
    return ReplayDecision(
        decision_id=decision_id,
        state_id=elimination.state_id,
        elimination_id=elimination_id,
        event_ticker=event_ticker,
        market_ticker=market,
        decision_at=elimination.known_at,
        simulated_arrival_at=arrival_at,
        decision=decision,
        reason=reason,
        requested_budget=budget,
        filled_qty=plan.filled_qty if plan else Decimal(0),
        gross_cost=plan.gross_cost if plan else Decimal(0),
        fee=plan.fee if plan else Decimal(0),
        total_cost=plan.total_cost if plan else Decimal(0),
        guaranteed_profit=plan.guaranteed_profit if plan else Decimal(0),
        guaranteed_roi=plan.guaranteed_roi if plan else Decimal(0),
        fills=plan.fills if plan else (),
        connection_id=book.connection_id if book else None,
        snapshot_id=book.snapshot_id if book else None,
        book_seq=book.last_seq if book else None,
    )


def _snapshot_side(levels: Sequence[Any], side_name: str, price_mode: str | None) -> dict[Decimal, Decimal]:
    out: dict[Decimal, Decimal] = {}
    for level in levels:
        if not isinstance(level, Sequence) or isinstance(level, (str, bytes)) or len(level) < 2:
            raise ValueError("invalid orderbook level")
        price = _native_price(side_name, str(level[0]), price_mode)
        qty = Decimal(str(level[1]))
        if price < 0 or price > ONE or qty < 0:
            raise ValueError("invalid orderbook level value")
        if qty > 0:
            out[price] = qty
    return out


def _native_price(side_name: str, raw_price: str, price_mode: str | None) -> Decimal:
    price = Decimal(raw_price)
    if side_name == "no" and price_mode == "unified_yes":
        price = ONE - price
    if price < 0 or price > ONE:
        raise ValueError("price outside [0,1]")
    return price


def _validate_binary_book(yes: Mapping[Decimal, Decimal], no: Mapping[Decimal, Decimal]) -> None:
    if yes and no and max(yes) + max(no) > ONE:
        raise ValueError("crossed binary book")


def _aware(value: Any) -> datetime:
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value
