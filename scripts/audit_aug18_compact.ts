import { pool } from "../lib/db";

async function main() {
  if (!pool) throw new Error("DATABASE_URL required");

  const coverage = await pool.query(`
    SELECT source, stid, count(*)::int AS n,
           min(captured_at) AS first_capture, max(captured_at) AS last_capture
    FROM weather_forecast_snapshots
    GROUP BY source, stid
    ORDER BY stid, source
  `);

  const events = await pool.query(`
    SELECT station_code, count(*)::int AS n,
           min(trade_date) AS first_date, max(trade_date) AS last_date,
           count(*) FILTER (WHERE final_value_f IS NOT NULL)::int AS settled
    FROM market_events
    WHERE station_code IN ('KNYC','KPHL','KLAX','KDEN','KSEA')
    GROUP BY station_code
    ORDER BY station_code
  `);

  const observations = await pool.query(`
    SELECT station_code, source, count(*)::int AS n,
           min(observed_at) AS first_obs, max(observed_at) AS last_obs
    FROM weather_observations
    WHERE station_code IN ('KNYC','KPHL','KLAX','KDEN','KSEA')
    GROUP BY station_code, source
    ORDER BY station_code, source
  `);

  const study = await pool.query(`
    WITH cfg(stid,tz) AS (
      VALUES
        ('KNYC','America/New_York'),
        ('KPHL','America/New_York'),
        ('KLAX','America/Los_Angeles'),
        ('KDEN','America/Denver'),
        ('KSEA','America/Los_Angeles')
    ),
    settled AS (
      SELECT e.event_ticker,e.station_code,e.trade_date,e.final_value_f,cfg.tz
      FROM market_events e
      JOIN cfg ON cfg.stid=e.station_code
      WHERE e.final_value_f IS NOT NULL
        AND e.trade_date >= DATE '2026-08-28'
    )
    SELECT e.station_code AS stid, e.trade_date, e.event_ticker,
           e.final_value_f::float8 AS final_high_f,
           win.ticker AS winning_ticker, win.label AS winning_label,
           win.lower_bound_f::float8 AS win_low_f, win.upper_bound_f::float8 AS win_high_f,
           twc.captured_at AS twc_captured_at,
           NULLIF(twc.daily_highs ->> e.trade_date::text,'')::float8 AS twc_high_f,
           nws.captured_at AS nws_captured_at,
           NULLIF(nws.daily_highs ->> e.trade_date::text,'')::float8 AS nws_high_f
    FROM settled e
    LEFT JOIN LATERAL (
      SELECT mc.ticker, mc.label, mc.lower_bound_f, mc.upper_bound_f
      FROM market_contracts mc
      WHERE mc.event_ticker=e.event_ticker AND mc.result='yes'
      ORDER BY mc.ticker
      LIMIT 1
    ) win ON true
    LEFT JOIN LATERAL (
      SELECT s.captured_at,s.daily_highs
      FROM weather_forecast_snapshots s
      WHERE s.stid=e.station_code AND s.source='twc'
        AND s.captured_at < (e.trade_date::timestamp AT TIME ZONE e.tz)
        AND s.daily_highs ? e.trade_date::text
      ORDER BY s.captured_at DESC
      LIMIT 1
    ) twc ON true
    LEFT JOIN LATERAL (
      SELECT s.captured_at,s.daily_highs
      FROM weather_forecast_snapshots s
      WHERE s.stid=e.station_code AND s.source='nws'
        AND s.captured_at < (e.trade_date::timestamp AT TIME ZONE e.tz)
        AND s.daily_highs ? e.trade_date::text
      ORDER BY s.captured_at DESC
      LIMIT 1
    ) nws ON true
    ORDER BY e.trade_date,e.station_code
  `);

  console.log("FORECAST_COVERAGE=" + JSON.stringify(coverage.rows));
  console.log("EVENT_COVERAGE=" + JSON.stringify(events.rows));
  console.log("OBS_COVERAGE=" + JSON.stringify(observations.rows));
  console.log("STUDY_ROWS=" + JSON.stringify(study.rows));
  await pool.end();
}

main().catch(async (e) => {
  console.error("FORECAST_STUDY_FAILED", e);
  await pool?.end().catch(() => undefined);
  process.exit(1);
});
