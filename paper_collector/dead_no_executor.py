from __future__ import annotations

"""Canonical benchmark executor for stale dead-NO opportunities.

Only candidates carrying a matching Step-4E bucket-elimination proof may enter
this module. The executor reconstructs the causal Kalshi L2 book at the
configured simulated-arrival time, calculates exact fee-adjusted guaranteed
terminal economics, and updates the paper portfolio only when the resulting
trade remains strictly profitable in settlement dollars.

There is deliberately no weather forecast, midpoint, candle, or probability
model here. Counterfactual research remains outside benchmark decisions/P&L.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any

import psycopg

import dead_no_execution as dne
import paper_engine as base

EXECUTION_MODEL_VERSION = "canonical-dead-no-paper-v1"


@dataclass(frozen=True)
class EvaluatedDeadNo:
    candidate: base.Candidate
    book: base.BookState
    asks: tuple[tuple[Decimal, Decimal], ...]
    plan: dne.DeadNoPlan
    latency_ms: int
    max_price: Decimal
    budget_at_evaluation: Decimal
    depth_notional: Decimal

    @property
    def best_ask(self) -> Decimal:
        return self.asks[0][0]


def canonical_dead_reason(candidate: base.Candidate) -> str | None:
    """Fail closed unless the candidate exactly matches its elimination proof."""
    evidence = candidate.evidence if isinstance(candidate.evidence, dict) else {}
    elimination = evidence.get("bucket_elimination")
    context = evidence.get("elimination_context")
    hard_state = evidence.get("hard_climate_state")
    if not isinstance(elimination, dict):
        return "MISSING_CANONICAL_ELIMINATION_PROOF"
    if not isinstance(context, dict):
        return "MISSING_ELIMINATION_CONTEXT"
    if not isinstance(hard_state, dict):
        return "MISSING_CANONICAL_HARD_STATE"
    if elimination.get("eliminated") is not True:
        return "MARKET_NOT_CANONICALLY_ELIMINATED"
    if str(elimination.get("event_ticker") or "") != candidate.event_ticker:
        return "ELIMINATION_EVENT_MISMATCH"
    if str(elimination.get("market_ticker") or "") != candidate.market_ticker:
        return "ELIMINATION_MARKET_MISMATCH"
    if str(elimination.get("station_code") or "") != candidate.station:
        return "ELIMINATION_STATION_MISMATCH"
    if str(context.get("event_ticker") or "") != candidate.event_ticker:
        return "ELIMINATION_CONTEXT_EVENT_MISMATCH"
    if candidate.market_ticker not in {
        str(value) for value in context.get("dead_market_tickers", []) if value
    }:
        return "MARKET_NOT_IN_DEAD_SET"
    rules_hash = str(evidence.get("event_rules_hash") or "")
    if not rules_hash or str(context.get("event_rules_hash") or "") != rules_hash:
        return "ELIMINATION_RULE_SNAPSHOT_MISMATCH"
    state_id = str(hard_state.get("state_id") or "")
    if not state_id or str(elimination.get("hard_state_id") or "") != state_id:
        return "ELIMINATION_HARD_STATE_MISMATCH"
    transition_id = str(hard_state.get("transition_evidence_id") or "")
    if not transition_id or str(context.get("transition_evidence_id") or "") != transition_id:
        return "ELIMINATION_TRANSITION_MISMATCH"
    if str(elimination.get("climate_date") or "") != str(hard_state.get("climate_date") or ""):
        return "ELIMINATION_CLIMATE_DATE_MISMATCH"
    try:
        bound = Decimal(str(elimination.get("hard_lower_bound_f")))
    except Exception:
        return "INVALID_ELIMINATION_BOUND"
    if bound != candidate.confirmed_high_f:
        return "ELIMINATION_BOUND_MISMATCH"
    return None


def _max_price(mode: dict[str, Any]) -> Decimal:
    cfg = mode.get("config", {}) if isinstance(mode.get("config"), dict) else {}
    return base.d(cfg.get("max_no_price", "1.00"))


def _latency_ms(mode: dict[str, Any]) -> int:
    cfg = mode.get("config", {}) if isinstance(mode.get("config"), dict) else {}
    return max(0, int(cfg.get("execution_latency_ms", 100)))


def _record_block(
    conn: psycopg.Connection[Any],
    mode: dict[str, Any],
    candidate: base.Candidate,
    reason: str,
    *,
    budget: Decimal = Decimal(0),
    details: dict[str, Any] | None = None,
) -> None:
    payload = {
        "mode": mode.get("mode_code"),
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "execution_math_version": dne.EXECUTION_MATH_VERSION,
    }
    if details:
        payload.update(details)
    base.record_decision(
        conn,
        int(mode["portfolio_id"]),
        candidate.signal_id,
        "blocked" if reason.startswith(("MISSING_", "ELIMINATION_", "MARKET_NOT_", "NO_VALID_L2")) else "skip",
        budget,
        reason,
        payload,
    )


def evaluate_for_mode(
    conn: psycopg.Connection[Any],
    candidate: base.Candidate,
    mode: dict[str, Any],
    global_cfg: dict[str, Any],
) -> EvaluatedDeadNo | None:
    """Evaluate one proven-dead candidate against causal executable L2."""
    invalid = canonical_dead_reason(candidate)
    if invalid is not None:
        _record_block(conn, mode, candidate, invalid)
        return None

    budget = base.mode_budget(conn, mode, candidate, global_cfg)
    if budget <= 0:
        _record_block(conn, mode, candidate, "PORTFOLIO_CAP_REACHED", budget=Decimal(0))
        return None

    latency_ms = _latency_ms(mode)
    arrival_ms = candidate.trigger_epoch_ms + latency_ms
    book = base.reconstruct_book(conn, candidate.market_ticker, arrival_ms)
    if book is None:
        _record_block(
            conn, mode, candidate, "NO_VALID_L2_AT_SIMULATED_ARRIVAL",
            budget=budget,
            details={"latency_ms": latency_ms, "arrival_ms": arrival_ms},
        )
        return None

    max_price = _max_price(mode)
    asks = tuple(base.no_asks(book, max_price))
    if not asks:
        _record_block(
            conn, mode, candidate, "NO_EXECUTABLE_NO_ASK_WITHIN_GUARD",
            budget=budget,
            details={"latency_ms": latency_ms, "max_price": max_price},
        )
        return None

    plan = dne.plan_dead_no(
        asks,
        budget=budget,
        fee_multiplier=candidate.fee_multiplier,
        max_price=max_price,
    )
    if plan is None:
        _record_block(
            conn, mode, candidate, "NO_POSITIVE_GUARANTEED_RETURN_AFTER_FEES",
            budget=budget,
            details={
                "latency_ms": latency_ms,
                "max_price": max_price,
                "best_ask": asks[0][0],
            },
        )
        return None

    depth_notional = sum((price * qty for price, qty in asks), Decimal(0))
    return EvaluatedDeadNo(
        candidate=candidate,
        book=book,
        asks=asks,
        plan=plan,
        latency_ms=latency_ms,
        max_price=max_price,
        budget_at_evaluation=budget,
        depth_notional=depth_notional,
    )


def _place_order(
    conn: psycopg.Connection[Any],
    mode: dict[str, Any],
    evaluated: EvaluatedDeadNo,
    budget: Decimal,
) -> Decimal:
    candidate = evaluated.candidate
    portfolio_id = int(mode["portfolio_id"])

    if canonical_dead_reason(candidate) is not None:
        _record_block(conn, mode, candidate, canonical_dead_reason(candidate) or "INVALID_ELIMINATION")
        return Decimal(0)

    existing = conn.execute(
        "SELECT 1 FROM paper_portfolio_decisions WHERE portfolio_id=%s AND signal_id=%s",
        (portfolio_id, candidate.signal_id),
    ).fetchone()
    if existing:
        return Decimal(0)

    plan = dne.plan_dead_no(
        evaluated.asks,
        budget=max(Decimal(0), budget),
        fee_multiplier=candidate.fee_multiplier,
        max_price=evaluated.max_price,
    )
    if plan is None:
        base.record_decision(
            conn, portfolio_id, candidate.signal_id, "skip", budget,
            "NO_POSITIVE_GUARANTEED_RETURN_AFTER_FEES",
            {
                "mode": mode.get("mode_code"),
                "execution_model_version": EXECUTION_MODEL_VERSION,
                "execution_math_version": dne.EXECUTION_MATH_VERSION,
                "max_price": evaluated.max_price,
                "best_ask": evaluated.best_ask,
            },
        )
        return Decimal(0)

    arrival_ms = candidate.trigger_epoch_ms + evaluated.latency_ms
    arrival_at = datetime.fromtimestamp(arrival_ms / 1000, tz=timezone.utc)
    requested_qty_fp = int((plan.filled_qty / dne.QTY_STEP).to_integral_value())
    max_price_fp = int((evaluated.max_price * Decimal(10000)).to_integral_value())
    gross_micros = int((plan.gross_cost * Decimal(1_000_000)).to_integral_value())
    fee_micros = int((plan.fee * Decimal(1_000_000)).to_integral_value())
    cost_micros = int((plan.total_cost * Decimal(1_000_000)).to_integral_value())

    decision_details = {
        "mode": mode.get("mode_code"),
        "allocation": (mode.get("config") or {}).get("allocation") if isinstance(mode.get("config"), dict) else None,
        "max_price": evaluated.max_price,
        "best_ask": evaluated.best_ask,
        "depth_notional": evaluated.depth_notional,
        "connection_id": evaluated.book.connection_id,
        "region": candidate.region,
        "guaranteed_payout": plan.guaranteed_payout,
        "guaranteed_profit": plan.guaranteed_profit,
        "guaranteed_roi": plan.guaranteed_roi,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "execution_math_version": dne.EXECUTION_MATH_VERSION,
        "elimination_id": candidate.evidence.get("bucket_elimination", {}).get("elimination_id"),
        "transition_evidence_id": candidate.evidence.get("hard_climate_state", {}).get("transition_evidence_id"),
    }
    if not base.record_decision(
        conn,
        portfolio_id,
        candidate.signal_id,
        "trade",
        budget,
        "EXECUTABLE_DEAD_NO_GUARANTEED",
        decision_details,
        plan.guaranteed_roi,
    ):
        return Decimal(0)

    audit = dict(candidate.evidence)
    audit["execution_guarantee"] = {
        "guaranteed_payout": plan.guaranteed_payout,
        "guaranteed_profit": plan.guaranteed_profit,
        "guaranteed_roi": plan.guaranteed_roi,
        "gross_cost": plan.gross_cost,
        "fee": plan.fee,
        "total_cost": plan.total_cost,
        "filled_qty": plan.filled_qty,
        "avg_fill_price": plan.avg_fill_price,
        "worst_fill_price": plan.worst_fill_price,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "execution_math_version": dne.EXECUTION_MATH_VERSION,
    }

    order = conn.execute(
        """
        INSERT INTO paper_orders(
          session_id,signal_id,portfolio_id,latency_profile_ms,strategy_code,market_ticker,outcome_side,
          requested_qty,decision_at,simulated_arrival_at,book_seq,status,avg_fill_price,filled_qty,
          gross_cost,estimated_fee,worst_price,book_snapshot,audit,
          requested_qty_fp,max_price_fp,filled_qty_fp,gross_cost_micros,fee_micros,total_cost_micros,
          state_known_through_epoch_ms,execution_model_version,fee_breakdown
        ) VALUES (
          %s,%s,%s,%s,'DBN',%s,'no',%s,%s,%s,%s,'filled',%s,%s,
          %s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
        ) RETURNING id
        """,
        (
            base.SESSION_ID,
            candidate.signal_id,
            portfolio_id,
            evaluated.latency_ms,
            candidate.market_ticker,
            plan.filled_qty,
            candidate.trigger_at,
            arrival_at,
            evaluated.book.last_seq,
            plan.avg_fill_price,
            plan.filled_qty,
            plan.gross_cost,
            plan.fee,
            plan.worst_fill_price,
            json.dumps({
                "connection_id": evaluated.book.connection_id,
                "snapshot_id": evaluated.book.snapshot_id,
                "snapshot_received_ms": evaluated.book.snapshot_received_ms,
                "arrival_ms": arrival_ms,
                "asks_used": [[format(p, "f"), format(q, "f")] for p, q in plan.fills],
                "l2_only": True,
            }, separators=(",", ":")),
            json.dumps(audit, separators=(",", ":"), default=base.json_default),
            requested_qty_fp,
            max_price_fp,
            requested_qty_fp,
            gross_micros,
            fee_micros,
            cost_micros,
            arrival_ms,
            EXECUTION_MODEL_VERSION,
            json.dumps(list(plan.fee_breakdown), separators=(",", ":")),
        ),
    ).fetchone()
    if not order:
        raise RuntimeError("dead-NO paper order insert failed")
    order_id = int(order[0])

    for index, (price, fill_qty) in enumerate(plan.fills):
        level_fee = Decimal(plan.fee_breakdown[index]["net_fee"]) if index < len(plan.fee_breakdown) else Decimal(0)
        conn.execute(
            """
            INSERT INTO paper_fills(order_id,price,qty,level_index,liquidity,estimated_fee)
            VALUES (%s,%s,%s,%s,'taker',%s)
            """,
            (order_id, price, fill_qty, index, level_fee),
        )

    conn.execute(
        """
        INSERT INTO paper_positions(portfolio_id,market_ticker,outcome_side,qty,cost_basis,fees_paid,status,updated_at)
        VALUES (%s,%s,'no',%s,%s,%s,'open',now())
        ON CONFLICT (portfolio_id,market_ticker,outcome_side)
        DO UPDATE SET qty=paper_positions.qty+EXCLUDED.qty,
                      cost_basis=paper_positions.cost_basis+EXCLUDED.cost_basis,
                      fees_paid=paper_positions.fees_paid+EXCLUDED.fees_paid,
                      status='open',updated_at=now()
        """,
        (portfolio_id, candidate.market_ticker, plan.filled_qty, plan.gross_cost, plan.fee),
    )
    cash_update = conn.execute(
        """
        UPDATE paper_portfolios
           SET cash_balance=cash_balance-%s,updated_at=now()
         WHERE id=%s AND cash_balance>=%s
        """,
        (plan.total_cost, portfolio_id, plan.total_cost),
    )
    if getattr(cash_update, "rowcount", 1) != 1:
        raise RuntimeError("paper cash update failed after dead-NO order")
    return plan.total_cost


def execute_candidates(
    conn: psycopg.Connection[Any],
    candidates: list[base.Candidate],
    modes: list[dict[str, Any]],
    global_cfg: dict[str, Any],
) -> None:
    """Execute only canonical dead-NO candidates using guaranteed economics."""
    if not candidates:
        return

    for mode in modes:
        evaluated: list[EvaluatedDeadNo] = []
        for candidate in candidates:
            item = evaluate_for_mode(conn, candidate, mode, global_cfg)
            if item is not None:
                evaluated.append(item)
        if not evaluated:
            continue

        allocation = str((mode.get("config") or {}).get("allocation", "best_edge_first"))
        if allocation == "depth_first":
            evaluated.sort(
                key=lambda item: (item.plan.total_cost, item.plan.guaranteed_roi, item.plan.guaranteed_profit),
                reverse=True,
            )
        else:
            # All other deterministic benchmark modes rank first by exact
            # fee-adjusted worst-case return, then by guaranteed dollars/capacity.
            evaluated.sort(
                key=lambda item: (item.plan.guaranteed_roi, item.plan.guaranteed_profit, item.plan.total_cost),
                reverse=True,
            )

        if allocation == "equal_risk":
            available = min(
                (base.mode_budget(conn, mode, item.candidate, global_cfg) for item in evaluated),
                default=Decimal(0),
            )
            target_each = available / Decimal(len(evaluated)) if evaluated else Decimal(0)
            for item in evaluated:
                allowed = min(target_each, base.mode_budget(conn, mode, item.candidate, global_cfg))
                _place_order(conn, mode, item, allowed)
        elif allocation == "edge_weighted":
            available = base.mode_budget(conn, mode, evaluated[0].candidate, global_cfg)
            total_score = sum((item.plan.guaranteed_roi for item in evaluated), Decimal(0))
            for item in evaluated:
                weight = (
                    item.plan.guaranteed_roi / total_score
                    if total_score > 0 else Decimal(1) / Decimal(len(evaluated))
                )
                target = available * weight
                allowed = min(target, base.mode_budget(conn, mode, item.candidate, global_cfg))
                _place_order(conn, mode, item, allowed)
        else:
            for item in evaluated:
                allowed = base.mode_budget(conn, mode, item.candidate, global_cfg)
                if allowed <= 0:
                    base.record_decision(
                        conn,
                        int(mode["portfolio_id"]),
                        item.candidate.signal_id,
                        "skip",
                        Decimal(0),
                        "PORTFOLIO_CAP_REACHED",
                        {
                            "mode": mode.get("mode_code"),
                            "execution_model_version": EXECUTION_MODEL_VERSION,
                        },
                        item.plan.guaranteed_roi,
                    )
                    continue
                _place_order(conn, mode, item, allowed)
