import { pool } from "../lib/db";
import { executeBacktest } from "../lib/backtest/run";
import { localDate } from "../lib/time";

const FROM = "2026-08-16";
const TO = "2026-08-18";

type ReplaySignalRow = {
  trade_date: string;
  station_code: string;
  event_ticker: string;
  contract_ticker: string;
  label: string;
  upper_bound_f: string | null;
  running_high_f: string;
  official_floor_f: string;
  action: string;
  entry_price: string | null;
  outcome: string | null;
  executable_proxy: boolean;
  net_profit_per_contract: string | null;
  limitation: string | null;
};

type LiveOrderRow = {
  order_id: string;
  session_id: string;
  mode_code: string | null;
  strategy_code: string;
  station_code: string;
  event_ticker: string | null;
  market_ticker: string;
  side: string;
  status: string;
  avg_fill_price: string | null;
  filled_qty: string;
  decision_at: Date;
  auditor_status: string;
  evidence: Record<string, unknown>;
  weather_observed_at: Date | null;
  weather_first_seen_at: Date | null;
  timezone: string | null;
  contract_label: string | null;
  contract_upper_bound_f: string | null;
};

const MONTHS: Record<string, string> = {
  JAN: "01", FEB: "02", MAR: "03", APR: "04", MAY: "05", JUN: "06",
  JUL: "07", AUG: "08", SEP: "09", OCT: "10", NOV: "11", DEC: "12",
};

function eventDateFromTicker(ticker: string | null) {
  if (!ticker) return null;
  const match = ticker.match(/-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(?:$|-)/i);
  if (!match) return null;
  const month = MONTHS[match[2].toUpperCase()];
  return month ? `20${match[1]}-${month}-${match[3]}` : null;
}

function numberOrNull(value: unknown) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function evidenceNumber(evidence: Record<string, unknown>, key: string) {
  return numberOrNull(evidence[key]);
}

function deadMarketListed(evidence: Record<string, unknown>, ticker: string) {
  const raw = evidence.dead_markets;
  return Array.isArray(raw) && raw.some((item) => String(item) === ticker);
}

async function main() {
  if (!pool) throw new Error("DATABASE_URL is required");

  const coverage = await pool.query<{
    trade_date: string;
    events: string;
    observations: string;
    quotes: string;
  }>(
    `SELECT d.trade_date::text,
            count(DISTINCT e.event_ticker)::text AS events,
            count(DISTINCT w.id)::text AS observations,
            count(DISTINCT q.contract_ticker || '|' || q.captured_at::text)::text AS quotes
       FROM generate_series($1::date,$2::date,interval '1 day') d(trade_date)
       LEFT JOIN market_events e ON e.trade_date=d.trade_date
       LEFT JOIN weather_observations w ON w.station_code=e.station_code
         AND (w.observed_at AT TIME ZONE 'UTC')::date BETWEEN d.trade_date-1 AND d.trade_date+1
       LEFT JOIN market_contracts mc ON mc.event_ticker=e.event_ticker
       LEFT JOIN market_quotes q ON q.contract_ticker=mc.ticker
      GROUP BY d.trade_date ORDER BY d.trade_date`,
    [FROM, TO],
  );

  const replay = await executeBacktest(FROM, TO);

  const signals = await pool.query<ReplaySignalRow>(
    `SELECT e.trade_date::text,
            b.station_code,b.event_ticker,b.contract_ticker,
            mc.label,mc.upper_bound_f::text,
            b.running_high_f::text,b.official_floor_f::text,b.action,
            b.entry_price::text,b.outcome,b.executable_proxy,
            b.net_profit_per_contract::text,b.limitation
       FROM backtest_signals b
       JOIN market_events e ON e.event_ticker=b.event_ticker
       JOIN market_contracts mc ON mc.ticker=b.contract_ticker
      WHERE b.run_id=$1
      ORDER BY e.trade_date,b.triggered_at,b.station_code,b.contract_ticker`,
    [replay.runId],
  );

  const replayAudit = signals.rows.map((row) => {
    const upper = numberOrNull(row.upper_bound_f);
    const floor = Number(row.official_floor_f);
    const logicallyDead = row.action === "BUY_NO" && upper !== null && floor > upper;
    return {
      date: row.trade_date,
      station: row.station_code,
      event: row.event_ticker,
      contract: row.contract_ticker,
      bucket: row.label,
      officialFloorF: floor,
      upperBoundF: upper,
      entry: numberOrNull(row.entry_price),
      executableProxy: row.executable_proxy,
      resolvedOutcome: row.outcome,
      netPerContract: numberOrNull(row.net_profit_per_contract),
      logicallyDead,
      limitation: row.limitation,
    };
  });

  const latestSession = await pool.query<{ id: string }>(
    `SELECT id FROM paper_sessions WHERE mode='paper_live' ORDER BY started_at DESC LIMIT 1`,
  );
  const sessionId = latestSession.rows[0]?.id ?? null;

  let liveOrders: Array<Record<string, unknown>> = [];
  let portfolioAudit: Array<Record<string, unknown>> = [];
  if (sessionId) {
    const orders = await pool.query<LiveOrderRow>(
      `SELECT o.id::text AS order_id,o.session_id,p.mode_code,o.strategy_code,
              s.station_code,s.event_ticker,o.market_ticker,o.outcome_side AS side,o.status,
              o.avg_fill_price::text,o.filled_qty::text,o.decision_at,
              s.auditor_status,s.evidence,
              w.observed_at AS weather_observed_at,w.first_seen_at AS weather_first_seen_at,
              st.timezone,mc.label AS contract_label,mc.upper_bound_f::text AS contract_upper_bound_f
         FROM paper_orders o
         JOIN paper_signals s ON s.id=o.signal_id
         LEFT JOIN live_weather_journal w ON w.id=s.weather_event_id
         LEFT JOIN stations st ON st.station_code=s.station_code
         LEFT JOIN paper_portfolios p ON p.id=o.portfolio_id
         LEFT JOIN market_contracts mc ON mc.ticker=o.market_ticker
        WHERE o.session_id=$1 AND o.status IN ('filled','partial')
        ORDER BY o.decision_at,o.id`,
      [sessionId],
    );

    liveOrders = orders.rows.map((row) => {
      const eventDate = eventDateFromTicker(row.event_ticker);
      const observedLocalDate = row.weather_observed_at && row.timezone
        ? localDate(row.weather_observed_at, row.timezone)
        : null;
      const dateMatches = eventDate !== null && observedLocalDate !== null
        ? eventDate === observedLocalDate
        : null;

      const confirmedHigh = evidenceNumber(row.evidence ?? {}, "confirmed_high_f");
      const upperFromEvidence = evidenceNumber(row.evidence ?? {}, "upper_bound_f");
      const upperFromContract = numberOrNull(row.contract_upper_bound_f);
      const upper = upperFromEvidence ?? upperFromContract;
      const hardNo = row.side === "no" && ["DBN", "DSN", "HSR", "HMF"].includes(row.strategy_code);
      const deadByBound = hardNo && confirmedHigh !== null && upper !== null ? confirmedHigh > upper : null;
      const deadByList = hardNo ? deadMarketListed(row.evidence ?? {}, row.market_ticker) : false;

      const issues: string[] = [];
      if (dateMatches === false) issues.push("WRONG_EVENT_DATE");
      if (row.auditor_status !== "approved") issues.push("NON_APPROVED_SIGNAL_EXECUTED");
      if (hardNo && deadByBound === false && !deadByList) issues.push("HARD_NO_NOT_PROVEN_DEAD");

      return {
        orderId: row.order_id,
        mode: row.mode_code,
        strategy: row.strategy_code,
        station: row.station_code,
        event: row.event_ticker,
        eventDate,
        weatherObservedLocalDate: observedLocalDate,
        market: row.market_ticker,
        bucket: row.contract_label,
        side: row.side,
        entry: numberOrNull(row.avg_fill_price),
        qty: Number(row.filled_qty),
        auditorStatus: row.auditor_status,
        confirmedHighF: confirmedHigh,
        upperBoundF: upper,
        hardNoDeadByBound: deadByBound,
        hardNoDeadBySignalEvidence: deadByList,
        issues,
      };
    });

    const portfolios = await pool.query<{
      mode_code: string;
      starting_bankroll: string;
      cash_balance: string;
      reserved_cash: string;
      realized_pnl: string;
      portfolio_id: string;
      order_count: string;
    }>(
      `SELECT p.mode_code,p.starting_bankroll::text,p.cash_balance::text,
              p.reserved_cash::text,p.realized_pnl::text,p.id::text AS portfolio_id,
              count(o.id)::text AS order_count
         FROM paper_portfolios p
         LEFT JOIN paper_orders o ON o.portfolio_id=p.id AND o.session_id=p.session_id
        WHERE p.session_id=$1
        GROUP BY p.id,p.mode_code,p.starting_bankroll,p.cash_balance,p.reserved_cash,p.realized_pnl
        ORDER BY p.mode_code`,
      [sessionId],
    );
    portfolioAudit = portfolios.rows.map((row) => ({
      mode: row.mode_code,
      portfolioId: row.portfolio_id,
      start: Number(row.starting_bankroll),
      cash: Number(row.cash_balance),
      reserved: Number(row.reserved_cash),
      realizedPnl: Number(row.realized_pnl),
      orders: Number(row.order_count),
      isolatedThousandDollarSleeve: Number(row.starting_bankroll) === 1000 && Number(row.cash_balance) >= 0,
    }));
  }

  const badReplay = replayAudit.filter((row) => !row.logicallyDead);
  const executableReplay = replayAudit.filter((row) => row.executableProxy);
  const liveIssues = liveOrders.filter((row) => Array.isArray(row.issues) && row.issues.length > 0);

  const byDay = [FROM, "2026-08-17", TO].map((date) => {
    const day = replayAudit.filter((row) => row.date === date);
    return {
      date,
      signals: day.length,
      executableProxy: day.filter((row) => row.executableProxy).length,
      logicallyValid: day.filter((row) => row.logicallyDead).length,
      resolvedWins: day.filter((row) => row.resolvedOutcome === "win").length,
      resolvedLosses: day.filter((row) => row.resolvedOutcome === "loss").length,
      netPerOneContract: day.reduce((sum, row) => sum + (row.netPerContract ?? 0), 0),
    };
  });

  console.log(JSON.stringify({
    audit: "Mercury Edge three-day replay",
    range: { from: FROM, to: TO },
    coverage: coverage.rows,
    replaySummary: replay,
    byDay,
    replayValidity: {
      totalSignals: replayAudit.length,
      executableProxySignals: executableReplay.length,
      logicallyInvalidSignals: badReplay.length,
      invalid: badReplay,
    },
    liveSession: sessionId,
    liveOrderAudit: {
      totalFilledOrPartial: liveOrders.length,
      ordersWithIssues: liveIssues.length,
      issues: liveIssues,
      orders: liveOrders,
    },
    portfolioAudit,
    limitations: [
      "Aug 16-17 historical executions use stored minute-candle quotes, so entry price is a quote proxy and historical L2 size/queue position is not proven.",
      "Today live paper orders use the captured L2 journal and are audited separately above.",
      "This harness evaluates hard-state dead-bucket correctness; experimental probabilistic strategies require their own causal calibration tests rather than treating short-run P&L as proof.",
    ],
  }, null, 2));

  await pool.end();
}

main().catch(async (error) => {
  console.error("THREE_DAY_REPLAY_FAILED", error);
  await pool?.end().catch(() => undefined);
  process.exit(1);
});
