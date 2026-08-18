import { NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

type ExecutionRow = {
  id: string;
  portfolio_id: string | null;
  mode_code: string | null;
  strategy_code: string;
  strategy_display_name: string | null;
  station_code: string;
  event_ticker: string | null;
  market_ticker: string;
  contract_label: string | null;
  lower_bound_f: string | null;
  upper_bound_f: string | null;
  outcome_side: "yes" | "no";
  status: "filled" | "partial";
  requested_qty: string;
  filled_qty: string;
  avg_fill_price: string | null;
  gross_cost: string;
  estimated_fee: string;
  worst_price: string | null;
  decision_at: Date;
  simulated_arrival_at: Date;
  position_status: "open" | "closed" | "settled" | null;
  position_qty: string | null;
  position_cost_basis: string | null;
  last_mark: string | null;
};

export async function GET() {
  if (!pool) {
    return NextResponse.json({ available: false, sessionId: null, generatedAt: new Date().toISOString(), trades: [] });
  }

  try {
    const session = await pool.query<{ id: string }>(
      "SELECT id FROM paper_sessions WHERE mode='paper_live' ORDER BY started_at DESC LIMIT 1",
    );
    const sessionId = session.rows[0]?.id ?? null;
    if (!sessionId) {
      return NextResponse.json({ available: true, sessionId: null, generatedAt: new Date().toISOString(), trades: [] });
    }

    const result = await pool.query<ExecutionRow>(
      `SELECT
         o.id::text,
         o.portfolio_id::text,
         p.mode_code,
         o.strategy_code,
         sc.display_name AS strategy_display_name,
         s.station_code,
         s.event_ticker,
         o.market_ticker,
         mc.label AS contract_label,
         mc.lower_bound_f::text,
         mc.upper_bound_f::text,
         o.outcome_side,
         o.status,
         o.requested_qty::text,
         o.filled_qty::text,
         o.avg_fill_price::text,
         o.gross_cost::text,
         o.estimated_fee::text,
         o.worst_price::text,
         o.decision_at,
         o.simulated_arrival_at,
         pos.status AS position_status,
         pos.qty::text AS position_qty,
         pos.cost_basis::text AS position_cost_basis,
         pos.last_mark::text
       FROM paper_orders o
       JOIN paper_signals s ON s.id = o.signal_id
       LEFT JOIN paper_portfolios p ON p.id = o.portfolio_id
       LEFT JOIN paper_strategy_configs sc ON sc.strategy_code = o.strategy_code
       LEFT JOIN market_contracts mc ON mc.ticker = o.market_ticker
       LEFT JOIN paper_positions pos
         ON pos.portfolio_id = o.portfolio_id
        AND pos.market_ticker = o.market_ticker
        AND pos.outcome_side = o.outcome_side
       WHERE o.session_id = $1
         AND o.status IN ('filled','partial')
       ORDER BY o.decision_at DESC, o.id DESC
       LIMIT 200`,
      [sessionId],
    );

    return NextResponse.json({
      available: true,
      sessionId,
      generatedAt: new Date().toISOString(),
      trades: result.rows.map((row) => ({
        id: row.id,
        portfolioId: row.portfolio_id,
        modeCode: row.mode_code,
        strategyCode: row.strategy_code,
        strategyName: row.strategy_display_name,
        stationCode: row.station_code,
        eventTicker: row.event_ticker,
        marketTicker: row.market_ticker,
        contractLabel: row.contract_label,
        lowerBoundF: row.lower_bound_f === null ? null : Number(row.lower_bound_f),
        upperBoundF: row.upper_bound_f === null ? null : Number(row.upper_bound_f),
        side: row.outcome_side,
        status: row.status,
        requestedQty: Number(row.requested_qty),
        filledQty: Number(row.filled_qty),
        entryPrice: row.avg_fill_price === null ? null : Number(row.avg_fill_price),
        grossCost: Number(row.gross_cost),
        fee: Number(row.estimated_fee),
        worstPrice: row.worst_price === null ? null : Number(row.worst_price),
        decisionAt: row.decision_at.toISOString(),
        arrivalAt: row.simulated_arrival_at.toISOString(),
        positionStatus: row.position_status,
        positionQty: row.position_qty === null ? null : Number(row.position_qty),
        positionCostBasis: row.position_cost_basis === null ? null : Number(row.position_cost_basis),
        lastMark: row.last_mark === null ? null : Number(row.last_mark),
      })),
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "paper executions unavailable" },
      { status: 500 },
    );
  }
}
