import { pool } from "../lib/db";
import { localDate } from "../lib/time";

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

async function main() {
  if (!pool) throw new Error("DATABASE_URL required");

  const rows = await pool.query<{
    order_id: string; session_id: string; session_started_at: Date; mode_code: string | null;
    strategy_code: string; station_code: string; event_ticker: string | null;
    market_ticker: string; outcome_side: string; status: string;
    avg_fill_price: string | null; filled_qty: string; gross_cost: string; estimated_fee: string;
    decision_at: Date; auditor_status: string; evidence: Record<string, unknown>;
    weather_id: string | null; weather_observed_at: Date | null; weather_first_seen_at: Date | null;
    timezone: string | null; contract_label: string | null; lower_bound_f: string | null; upper_bound_f: string | null;
  }>(
    `SELECT o.id::text AS order_id,o.session_id,ps.started_at AS session_started_at,p.mode_code,
            o.strategy_code,s.station_code,s.event_ticker,o.market_ticker,o.outcome_side,o.status,
            o.avg_fill_price::text,o.filled_qty::text,o.gross_cost::text,o.estimated_fee::text,
            o.decision_at,s.auditor_status,s.evidence,s.weather_event_id::text AS weather_id,
            w.observed_at AS weather_observed_at,w.first_seen_at AS weather_first_seen_at,
            st.timezone,mc.label AS contract_label,mc.lower_bound_f::text,mc.upper_bound_f::text
       FROM paper_orders o
       JOIN paper_sessions ps ON ps.id=o.session_id
       JOIN paper_signals s ON s.id=o.signal_id
       LEFT JOIN paper_portfolios p ON p.id=o.portfolio_id
       LEFT JOIN live_weather_journal w ON w.id=s.weather_event_id
       LEFT JOIN stations st ON st.station_code=s.station_code
       LEFT JOIN market_contracts mc ON mc.ticker=o.market_ticker
      WHERE ps.mode='paper_live'
        AND o.status IN ('filled','partial')
        AND o.decision_at >= '2026-08-18T00:00:00Z'::timestamptz
        AND o.decision_at <  '2026-08-19T12:00:00Z'::timestamptz
      ORDER BY o.decision_at,o.id`,
  );

  const audited = rows.rows.map((r) => {
    const eventDate = eventDateFromTicker(r.event_ticker);
    const weatherDate = r.weather_observed_at && r.timezone ? localDate(r.weather_observed_at, r.timezone) : null;
    const evidence = r.evidence ?? {};
    const confirmedHigh = num(evidence.confirmed_high_f);
    const evidenceUpper = num(evidence.upper_bound_f);
    const contractUpper = num(r.upper_bound_f);
    const upper = evidenceUpper ?? contractUpper;
    const deadMarkets = Array.isArray(evidence.dead_markets) ? evidence.dead_markets.map(String) : [];
    const hardNo = r.outcome_side === 'no' && ['DBN','DSN','HSR','HMF'].includes(r.strategy_code);
    const issues: string[] = [];
    if (eventDate && weatherDate && eventDate !== weatherDate) issues.push('WRONG_EVENT_DATE');
    if (r.auditor_status !== 'approved') issues.push('NON_APPROVED_SIGNAL_EXECUTED');
    if (hardNo && confirmedHigh !== null && upper !== null && confirmedHigh <= upper && !deadMarkets.includes(r.market_ticker)) {
      issues.push('HARD_NO_NOT_PROVEN_DEAD');
    }
    return {
      orderId: r.order_id,
      sessionId: r.session_id,
      sessionStartedAt: r.session_started_at.toISOString(),
      decisionAt: r.decision_at.toISOString(),
      mode: r.mode_code,
      strategy: r.strategy_code,
      station: r.station_code,
      event: r.event_ticker,
      eventDate,
      weatherId: r.weather_id,
      weatherObservedAt: r.weather_observed_at?.toISOString() ?? null,
      weatherFirstSeenAt: r.weather_first_seen_at?.toISOString() ?? null,
      weatherLocalDate: weatherDate,
      market: r.market_ticker,
      bucket: r.contract_label,
      lowerBoundF: num(r.lower_bound_f),
      upperBoundF: upper,
      side: r.outcome_side,
      entry: num(r.avg_fill_price),
      qty: Number(r.filled_qty),
      cost: Number(r.gross_cost),
      fee: Number(r.estimated_fee),
      auditorStatus: r.auditor_status,
      confirmedHighF: confirmedHigh,
      deadListed: deadMarkets.includes(r.market_ticker),
      issues,
    };
  });

  const sessions = new Map<string, {orders:number; issues:number; wrongDate:number; nonApproved:number}>();
  for (const o of audited) {
    const s = sessions.get(o.sessionId) ?? {orders:0,issues:0,wrongDate:0,nonApproved:0};
    s.orders += 1;
    if (o.issues.length) s.issues += 1;
    if (o.issues.includes('WRONG_EVENT_DATE')) s.wrongDate += 1;
    if (o.issues.includes('NON_APPROVED_SIGNAL_EXECUTED')) s.nonApproved += 1;
    sessions.set(o.sessionId,s);
  }

  const wrongDate = audited.filter((o) => o.issues.includes('WRONG_EVENT_DATE'));
  const nonApproved = audited.filter((o) => o.issues.includes('NON_APPROVED_SIGNAL_EXECUTED'));
  const target = audited.filter((o) => o.market === 'KXHIGHNY-26AUG19-T85');

  console.log(JSON.stringify({
    audit: 'Aug 18 all paper sessions',
    totalOrders: audited.length,
    ordersWithIssues: audited.filter((o) => o.issues.length).length,
    wrongEventDateOrders: wrongDate.length,
    nonApprovedExecutedOrders: nonApproved.length,
    sessions: Object.fromEntries(sessions),
    targetKnycAug19T85: target,
    wrongDateOrders: wrongDate,
    nonApprovedOrders: nonApproved,
    allOrders: audited,
  }, null, 2));
  await pool.end();
}

main().catch(async (error) => {
  console.error('AUG18_AUDIT_FAILED', error);
  await pool?.end().catch(() => undefined);
  process.exit(1);
});
