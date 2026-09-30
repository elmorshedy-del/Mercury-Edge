import { NextRequest, NextResponse } from "next/server";
import { hasDatabase, query } from "@/lib/db";
import { STATIONS } from "@/lib/config";

export const dynamic = "force-dynamic";

type BaselineRow = {
  local_date: string;
  captured_at: Date;
  issued_at: Date | null;
  forecast_high: number | null;
};

type SnapshotRow = {
  captured_at: Date;
  issued_at: Date | null;
  forecast_high: number | null;
};

const DASHBOARD_IDS = new Set(["KNYC", "KPHL", "KLAX", "KDEN", "KSEA"]);

function validDate(value: string | null) {
  return Boolean(value && /^\d{4}-\d{2}-\d{2}$/.test(value));
}

export async function GET(request: NextRequest) {
  const stid = request.nextUrl.searchParams.get("stid")?.toUpperCase() ?? "KNYC";
  const date = request.nextUrl.searchParams.get("date");

  if (!DASHBOARD_IDS.has(stid)) {
    return NextResponse.json({ error: `Unsupported station ${stid}` }, { status: 400 });
  }
  if (!validDate(date)) {
    return NextResponse.json({ error: "A YYYY-MM-DD date is required" }, { status: 400 });
  }

  const config = STATIONS.find((item) => item.station === stid);
  if (!config) return NextResponse.json({ error: `Station config missing for ${stid}` }, { status: 400 });

  if (!hasDatabase) {
    return NextResponse.json({
      stid,
      city: config.city,
      date,
      timezone: config.timezone,
      baseline: null,
      message: "Historical TWC snapshots require the dashboard database.",
    });
  }

  try {
    const baseline = await query<BaselineRow>(
      `SELECT local_date::text, captured_at, issued_at, forecast_high
       FROM weather_trajectory_baselines_v2
       WHERE stid = $1 AND local_date = $2::date AND source = 'twc'
       LIMIT 1`,
      [stid, date],
    );

    let row = baseline.rows[0] ?? null;
    let provenance = "daily frozen baseline";

    if (!row) {
      const snapshot = await query<SnapshotRow>(
        `SELECT captured_at, issued_at,
                NULLIF(daily_highs ->> ($2::text), '')::real AS forecast_high
         FROM weather_forecast_snapshots
         WHERE stid = $1 AND source = 'twc'
           AND captured_at < ($2::date::timestamp AT TIME ZONE $3)
           AND daily_highs ? ($2::text)
         ORDER BY captured_at DESC
         LIMIT 1`,
        [stid, date, config.timezone],
      );
      const snap = snapshot.rows[0] ?? null;
      if (snap) {
        row = {
          local_date: date as string,
          captured_at: snap.captured_at,
          issued_at: snap.issued_at,
          forecast_high: snap.forecast_high,
        };
        provenance = "latest pre-day snapshot";
      }
    }

    return NextResponse.json({
      stid,
      city: config.city,
      date,
      timezone: config.timezone,
      baseline: row ? {
        source: "twc",
        forecastHigh: row.forecast_high,
        capturedAt: row.captured_at.toISOString(),
        issuedAt: row.issued_at?.toISOString() ?? null,
        provenance,
      } : null,
    }, { headers: { "Cache-Control": "no-store, max-age=0, must-revalidate" } });
  } catch (error) {
    console.error("Historical weather baseline lookup failed", error);
    return NextResponse.json({
      error: error instanceof Error ? error.message : "Unable to load historical weather baseline",
    }, { status: 502 });
  }
}
