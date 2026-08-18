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

type JournalRow = {
  market_ticker: string;
  id: string;
  channel: "orderbook_snapshot" | "orderbook_delta";
  received_at: Date;
  exchange_ts_ms: string | null;
  raw_text: string;
  price_mode: string | null;
};

type TradeJournalRow = {
  market_ticker: string;
  received_at: Date;
  exchange_ts_ms: string | null;
  raw_text: string;
};

type LiveMark = {
  yesBid: number | null;
  noBid: number | null;
  yesAsk: number | null;
  noAsk: number | null;
  receivedAt: string;
  exchangeAt: string | null;
};

type LastTrade = {
  yesPrice: number | null;
  noPrice: number | null;
  receivedAt: string;
  exchangeAt: string | null;
};

function nativePrice(side: "yes" | "no", raw: unknown, priceMode: string | null) {
  const price = Number(raw);
  if (!Number.isFinite(price)) return null;
  // With use_yes_price=true, NO-side orderbook levels are transmitted on the
  // YES-leg scale. Convert them back to native NO bids before deriving asks.
  return side === "no" && priceMode === "unified_yes" ? 1 - price : price;
}

function reconstructMarks(rows: JournalRow[]) {
  const books = new Map<string, {
    yes: Map<number, number>;
    no: Map<number, number>;
    receivedAt: string;
    exchangeAt: string | null;
  }>();

  for (const row of rows) {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(row.raw_text) as Record<string, unknown>;
    } catch {
      continue;
    }
    const msg = payload.msg && typeof payload.msg === "object"
      ? payload.msg as Record<string, unknown>
      : {};
    const ticker = String(msg.market_ticker ?? row.market_ticker ?? "");
    if (!ticker) continue;

    const existing = books.get(ticker) ?? {
      yes: new Map<number, number>(),
      no: new Map<number, number>(),
      receivedAt: row.received_at.toISOString(),
      exchangeAt: null,
    };

    if (row.channel === "orderbook_snapshot") {
      existing.yes.clear();
      existing.no.clear();
      for (const side of ["yes", "no"] as const) {
        const levels = msg[`${side}_dollars_fp`];
        if (!Array.isArray(levels)) continue;
        const bookSide = side === "yes" ? existing.yes : existing.no;
        for (const level of levels) {
          if (!Array.isArray(level) || level.length < 2) continue;
          const price = nativePrice(side, level[0], row.price_mode);
          const qty = Number(level[1]);
          if (price === null || !Number.isFinite(qty) || qty <= 0) continue;
          bookSide.set(price, qty);
        }
      }
    } else {
      const side = msg.side === "yes" || msg.side === "no" ? msg.side : null;
      if (side) {
        const price = nativePrice(side, msg.price_dollars, row.price_mode);
        const delta = Number(msg.delta_fp);
        if (price !== null && Number.isFinite(delta)) {
          const bookSide = side === "yes" ? existing.yes : existing.no;
          const next = (bookSide.get(price) ?? 0) + delta;
          if (next > 0) bookSide.set(price, next);
          else bookSide.delete(price);
        }
      }
    }

    existing.receivedAt = row.received_at.toISOString();
    existing.exchangeAt = row.exchange_ts_ms === null
      ? existing.exchangeAt
      : new Date(Number(row.exchange_ts_ms)).toISOString();
    books.set(ticker, existing);
  }

  const marks = new Map<string, LiveMark>();
  for (const [ticker, book] of books) {
    const yesPrices = [...book.yes.keys()];
    const noPrices = [...book.no.keys()];
    const yesBid = yesPrices.length ? Math.max(...yesPrices) : null;
    const noBid = noPrices.length ? Math.max(...noPrices) : null;
    marks.set(ticker, {
      yesBid,
      noBid,
      yesAsk: noBid === null ? null : 1 - noBid,
      noAsk: yesBid === null ? null : 1 - yesBid,
      receivedAt: book.receivedAt,
      exchangeAt: book.exchangeAt,
    });
  }
  return marks;
}

function parseLastTrades(rows: TradeJournalRow[]) {
  const trades = new Map<string, LastTrade>();
  for (const row of rows) {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(row.raw_text) as Record<string, unknown>;
    } catch {
      continue;
    }
    const msg = payload.msg && typeof payload.msg === "object"
      ? payload.msg as Record<string, unknown>
      : {};
    const yesPrice = Number(msg.yes_price_dollars);
    const noPrice = Number(msg.no_price_dollars);
    trades.set(row.market_ticker, {
      yesPrice: Number.isFinite(yesPrice) ? yesPrice : null,
      noPrice: Number.isFinite(noPrice) ? noPrice : null,
      receivedAt: row.received_at.toISOString(),
      exchangeAt: row.exchange_ts_ms === null ? null : new Date(Number(row.exchange_ts_ms)).toISOString(),
    });
  }
  return trades;
}

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

    const tickers = [...new Set(result.rows.map((row) => row.market_ticker))];
    let liveMarks = new Map<string, LiveMark>();
    let lastTrades = new Map<string, LastTrade>();

    if (tickers.length) {
      const [journal, trades] = await Promise.all([
        pool.query<JournalRow>(
          `WITH latest_snapshot AS (
             SELECT DISTINCT ON (market_ticker)
                    market_ticker,id,connection_id
               FROM market_data_journal
              WHERE session_id=$1
                AND market_ticker=ANY($2::text[])
                AND channel='orderbook_snapshot'
              ORDER BY market_ticker,received_epoch_ms DESC,id DESC
           )
           SELECT j.market_ticker,j.id::text,j.channel,j.received_at,j.exchange_ts_ms::text,j.raw_text,j.price_mode
             FROM latest_snapshot s
             JOIN market_data_journal j
               ON j.session_id=$1
              AND j.market_ticker=s.market_ticker
              AND j.connection_id=s.connection_id
              AND j.id>=s.id
            WHERE j.channel IN ('orderbook_snapshot','orderbook_delta')
            ORDER BY j.market_ticker,j.id ASC`,
          [sessionId, tickers],
        ),
        pool.query<TradeJournalRow>(
          `SELECT DISTINCT ON (market_ticker)
                  market_ticker,received_at,exchange_ts_ms::text,raw_text
             FROM market_data_journal
            WHERE session_id=$1
              AND market_ticker=ANY($2::text[])
              AND channel='trade'
            ORDER BY market_ticker,received_epoch_ms DESC,id DESC`,
          [sessionId, tickers],
        ),
      ]);
      liveMarks = reconstructMarks(journal.rows);
      lastTrades = parseLastTrades(trades.rows);
    }

    return NextResponse.json({
      available: true,
      sessionId,
      generatedAt: new Date().toISOString(),
      trades: result.rows.map((row) => {
        const entryPrice = row.avg_fill_price === null ? null : Number(row.avg_fill_price);
        const filledQty = Number(row.filled_qty);
        const grossCost = Number(row.gross_cost);
        const entryFee = Number(row.estimated_fee);
        const mark = liveMarks.get(row.market_ticker) ?? null;
        const tradePrint = lastTrades.get(row.market_ticker) ?? null;

        const liveBid = row.outcome_side === "yes" ? mark?.yesBid ?? null : mark?.noBid ?? null;
        const liveAsk = row.outcome_side === "yes" ? mark?.yesAsk ?? null : mark?.noAsk ?? null;
        const lastTradePrice = row.outcome_side === "yes"
          ? tradePrint?.yesPrice ?? null
          : tradePrint?.noPrice ?? null;

        // P&L is deliberately marked to the executable bid, not to the ask or
        // last trade. This is the price the paper position could sell into now.
        const priceChange = liveBid === null || entryPrice === null ? null : liveBid - entryPrice;
        const priceChangePct = priceChange === null || entryPrice === null || entryPrice <= 0
          ? null
          : priceChange / entryPrice;
        const markPnl = liveBid === null
          ? null
          : liveBid * filledQty - grossCost - entryFee;

        return {
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
          filledQty,
          entryPrice,
          grossCost,
          fee: entryFee,
          worstPrice: row.worst_price === null ? null : Number(row.worst_price),
          decisionAt: row.decision_at.toISOString(),
          arrivalAt: row.simulated_arrival_at.toISOString(),
          positionStatus: row.position_status,
          positionQty: row.position_qty === null ? null : Number(row.position_qty),
          positionCostBasis: row.position_cost_basis === null ? null : Number(row.position_cost_basis),
          lastMark: row.last_mark === null ? null : Number(row.last_mark),
          latestPrice: liveBid,
          liveBid,
          liveAsk,
          latestPriceAt: mark?.receivedAt ?? null,
          latestExchangeAt: mark?.exchangeAt ?? null,
          lastTradePrice,
          lastTradeAt: tradePrint?.exchangeAt ?? tradePrint?.receivedAt ?? null,
          priceChange,
          priceChangePct,
          markPnl,
          markMethod: liveBid === null ? null : "executable_bid",
        };
      }),
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "paper executions unavailable" },
      { status: 500 },
    );
  }
}
