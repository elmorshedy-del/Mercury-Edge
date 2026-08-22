from __future__ import annotations

"""Small runtime patches that harden existing deterministic execution code.

Keeping these overrides isolated makes the original engine replayable byte-for-
byte while live paper execution gets corrected risk accounting, fee-adjusted
ranking, and independent DBN counterfactual trials.
"""

from decimal import Decimal

import paper_engine as dbn
import risk_controls
import shadow_lab
import strategy_engines as se

_ORIGINAL_EXECUTE_CANDIDATES = dbn.execute_candidates


def fee_adjusted_dbn_evaluate(conn, candidate: dbn.Candidate, mode):
    cfg = mode["config"]
    latency_ms = max(0, int(cfg.get("execution_latency_ms", 100)))
    max_price = dbn.d(cfg.get("max_no_price", "0.93"))
    arrival_ms = candidate.trigger_epoch_ms + latency_ms
    book = dbn.reconstruct_book(conn, candidate.market_ticker, arrival_ms)
    if not book:
        return None
    asks = dbn.no_asks(book, max_price)
    if not asks:
        return None
    best = asks[0][0]
    unit_cost, _, _ = dbn.total_cost([(best, Decimal("1"))], candidate.fee_multiplier)
    net_edge = Decimal("1") - unit_cost
    if unit_cost <= 0 or net_edge <= 0:
        return None
    net_roi = net_edge / unit_cost
    depth = sum((price * qty for price, qty in asks), Decimal(0))
    return dbn.EvaluatedCandidate(
        candidate,
        book,
        asks,
        best,
        depth,
        net_roi,
        net_edge,
        latency_ms,
        max_price,
    )


def execute_candidates_with_dbn_lab(conn, candidates, modes, global_cfg):
    configs = se.strategy_configs(conn)
    cfg = configs.get("DBN", {})
    if bool(cfg.get("shadow_enabled", True)):
        max_price = se.d(cfg.get("config", {}).get("max_entry_price", 0.97))
        for candidate in candidates:
            bundle = se.Bundle(
                strategy_code="DBN",
                signal_class="hard_state",
                station=candidate.station,
                region=candidate.region,
                event_ticker=candidate.event_ticker,
                weather_id=int(candidate.evidence.get("weather_event_id") or 0),
                trigger_at=candidate.trigger_at,
                trigger_epoch_ms=candidate.trigger_epoch_ms,
                legs=[se.Leg(candidate.market_ticker, "no", candidate.trigger_epoch_ms, max_price, "dead_bucket")],
                evidence=dict(candidate.evidence),
                auditor_status="approved",
                blocked_reason=None,
            )
            shadow_lab.record_bundle_trials(
                conn,
                session_id=dbn.SESSION_ID,
                bundle=bundle,
                signal_id=candidate.signal_id,
                global_cfg=global_cfg,
                strategy_cfg=cfg,
                fee_multiplier=candidate.fee_multiplier,
            )
    return _ORIGINAL_EXECUTE_CANDIDATES(conn, candidates, modes, global_cfg)


# Event/region exposure must be current open risk, not lifetime historical spend.
dbn.exposure = risk_controls.exposure
se.exposure = risk_controls.exposure

# Rank and weight hard-state DBN opportunities on fee-adjusted net edge.
dbn.evaluate_for_mode = fee_adjusted_dbn_evaluate

# Evaluate the benchmark strategy in many alternative latency/size universes
# without taking cash from any of the six benchmark portfolios.
dbn.execute_candidates = execute_candidates_with_dbn_lab
