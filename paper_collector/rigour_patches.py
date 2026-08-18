from __future__ import annotations

"""Small runtime patches that harden existing deterministic execution code.

Keeping these overrides isolated makes the original engine replayable byte-for-
byte while live paper execution gets corrected risk accounting and fee-adjusted
ranking.
"""

from decimal import Decimal

import paper_engine as dbn
import risk_controls
import strategy_engines as se


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


# Event/region exposure must be current open risk, not lifetime historical spend.
dbn.exposure = risk_controls.exposure
se.exposure = risk_controls.exposure

# Rank and weight hard-state DBN opportunities on fee-adjusted net edge.
dbn.evaluate_for_mode = fee_adjusted_dbn_evaluate
