import { pool } from "../lib/db";
import { executeBacktest } from "../lib/backtest/run";
import { localDate } from "../lib/time";

const DATES = ["2026-08-16", "2026-08-17", "2026-08-18"];
const MONTHS: Record<string, string> = {
  JAN: "01", FEB: "02", MAR: "03", APR: "04", MAY: "05", JUN: "06",
  JUL: "07", AUG: "08", SEP: "09", OCT: "10", NOV: "11", DEC: "12",
};

function eventDateFromTicker(ticker: string | null) {
  if (!ticker) return null;
  const m = ticker.match(/-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(?:$|-)/i);
  if (!m) return null;
  return `20${m[1]}-${MONTHS[m[2].toUpperCase()]}-${m[3]}`;
}

function num(v: unknown) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

async function replayDay(date: string) {
  if (!pool) throw new Error("DATABASE_URL is required");
  const run = await executeBacktest(date, date);
  const rows = await pool.query<{
    station_code: string;
    event_ticker: string;
    contract_ticker: string;
    label: string;
    upper_bound_f: string | null;
    official_floor_f: string;
    entry_price: string | null;
    outcome: string | null;
    executable_proxy: boolean;
    net_profit_per_contract: string | null;
  }>(
    `SELECT b.station_code,b.event_ticker,b.contract_ticker,mc.label,
            mc.upper_bound_f::text,b.official_floor_f::text,b.entry_price::text,
            b.outcome,b.executable_proxy,b.net_profit_per_contract::text
       FROM backtest_signals b
       JOIN market_contracts mc ON mc.ticker=b.contract_ticker
      WHERE b.run_id=$1
      ORDER BY b.triggered_at,b.station_code,b.contract_ticker`,
    [run.runId],
  );

  const trades = rows.rows.map((r) => {
    const upper = num(r.upper_bound_f);
    const floor = Number(r.official_floor_f);
    return {
      station: r.station_code,
      event: r.event_ticker,
      market: r.contract_ticker,
      bucket: r.label,
      officialFloorF: floor,
      upperBoundF: upper,
      logicallyDead: upper !== null && floor > upper,
      entry: num(r.entry_price),
      executableProxy: r.executable_proxy,
      outcome: r.outcome,
      netPerContract: num(r.net_profit_per_contract),
    };
  });

  return {
    date,
    run,
    trades,
    invalidLogic: trades.filter((t) => !t.logicallyDead),
  };
}

async function auditLive() {
  if (!pool) throw new Error("DATABASE_URL is required");
  const s = await pool.query<{ id: string }>(
    `SELECT id FROM paper_sessions WHERE mode='paper_live' ORDER BY started_at DESC LIMIT 1`,
  );
  const sessionId = s.rows[0]?.id ?? null;
  if (!sessionId) return { sessionId: null, orders: [], issues: [], portfolios: [] };

  const orders = await pool.query<{
    id: string; mode_code: string | null; strategy_code: string; station_code: string;
    event_ticker: string | null; market_ticker: string; outcome_side: string;
    avg_fill_price: string | null; filled_qty: string; auditor_status: string;
    evidence: Record<string, unknown>; observed_at: Date | null; timezone: string | null;
  }>(
    `SELECT o.id::text,p.mode_code,o.strategy_code,s.station_code,s.event_ticker,
            o.market_ticker,o.outcome_side,o.avg_fill_price::text,o.filled_qty::text,
            s.auditor_status,s.evidence,w.observed_at,st.timezone
       FROM paper_orders o
       JOIN paper_signals s ON s.id=o.signal_id
       LEFT JOIN paper_portfolios p ON p.id=o.portfolio_id
       LEFT JOIN live_weather_journal w ON w.id=s.weather_event_id
       LEFT JOIN stations st ON st.station_code=s.station_code
      WHERE o.session_id=$1 AND o.status IN ('filled','partial')
      ORDER BY o.decision_at,o.id`,
    [sessionId],
  );

  const audited = orders.rows.map((r) => {
    const eventDate = eventDateFromTicker(r.event_ticker);
    const weatherDate = r.observed_at && r.timezone ? localDate(r.observed_at, r.timezone) : null;
    const evidence = r.evidence ?? {};
    const confirmedHigh = num(evidence.confirmed_high_f);
    const upper = num(evidence.upper_bound_f);
    const deadList = Array.isArray(evidence.dead_markets)
      ? evidence.dead_markets.map(String)
      : [];
    const hardNo = r.outcome_side === "no" && ["DBN", "DSN", "HSR", "HMF"].includes(r.strategy_code);
    const issues: string[] = [];
    if (eventDate && weatherDate && eventDate !== weatherDate) issues.push("WRONG_EVENT_DATE");
    if (r.auditor_status !== "approved") issues.push("NON_APPROVED_SIGNAL_EXECUTED");
    if (hardNo && upper !== null && confirmedHigh !== null && confirmedHigh <= upper && !deadList.includes(r.market_ticker)) {
      issues.push("HARD_NO_NOT_PROVEN_DEAD");
    }
    return {
      orderId: r.id,
      mode: r.mode_code,
      strategy: r.strategy_code,
      station: r.station_code,
      event: r.event_ticker,
      eventDate,
      weatherDate,
      market: r.market_ticker,
      side: r.outcome_side,
      entry: num(r.avg_fill_price),
      qty: Number(r.filled_qty),
      auditorStatus: r.auditor_status,
      confirmedHighF: confirmedHigh,
      upperBoundF: upper,
      issues,
    };
  });

  const portfolios = await pool.query<{
    mode_code: string; id: string; starting_bankroll: string; cash_balance: string;
    reserved_cash: string; realized_pnl: string;
  }>(
    `SELECT mode_code,id::text,starting_bankroll::text,cash_balance::text,
            reserved_cash::text,realized_pnl::text
       FROM paper_portfolios WHERE session_id=$1 ORDER BY mode_code`,
    [sessionId],
  );

  return {
    sessionId,
    orders: audited,
    issues: audited.filter((o) => o.issues.length > 0),
    portfolios: portfolios.rows.map((p) => ({
      mode: p.mode_code,
      id: p.id,
      start: Number(p.starting_bankroll),
      cash: Number(p.cash_balance),
      reserved: Number(p.reserved_cash),
      realizedPnl: Number(p.realized_pnl),
      validIndependentSleeve: Number(p.starting_bankroll) === 1000 && Number(p.cash_balance) >= 0,
    })),
  };
}

async function main() {
  if (!pool) throw new Error("DATABASE_URL is required");
  const days = [];
  for (const date of DATES) {
    days.push(await replayDay(date));
  }
  const live = await auditLive();
  console.log(JSON.stringify({
    audit: "Mercury Edge 3-day logic replay",
    days,
    live,
    limitations: [
      "Historical Aug 16-17 entries are minute-candle quote proxies, not proof of L2 fill size or queue position.",
      "Today actual paper orders are audited from the live paper tables separately.",
      "Short-run P&L cannot validate probabilistic strategy calibration; this pass primarily tests deterministic trade validity and data association.",
    ],
  }, null, 2));
  await pool.end();
}

main().catch(async (error) => {
  console.error("LIGHT_REPLAY_FAILED", error);
  await pool?.end().catch(() => undefined);
  process.exit(1);
});
