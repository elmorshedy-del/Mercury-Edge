"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type WeatherRow = {
  time: string;
  high6: number | null;
  low6: number | null;
  raw: string | null;
  source?: string | null;
  receivedAt?: string | null;
};

type Station = {
  stid: string;
  city: string;
  timezone: string | null;
  sixHour: WeatherRow[];
};

type DashboardData = {
  updatedAt: string;
  stations: Station[];
};

const DUE_GRACE_MS = 20 * 60 * 1000;
const SIX_HOURS_MS = 6 * 60 * 60 * 1000;

function fmtTemp(value: number | null) {
  return value === null ? "—" : `${value.toFixed(1)}°F`;
}

function fmtTime(iso: string, timezone: string | null, seconds = false) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
      second: seconds ? "2-digit" : undefined,
      timeZoneName: "short",
      timeZone: timezone ?? undefined,
    }).format(new Date(iso));
  } catch {
    return "—";
  }
}

function maxCode(raw: string | null) {
  return raw?.match(/\b1[01]\d{3}\b/)?.[0] ?? null;
}

function minCode(raw: string | null) {
  return raw?.match(/\b2[01]\d{3}\b/)?.[0] ?? null;
}

function scheduleFromLatest(station: Station, nowMs: number) {
  const latest = station.sixHour?.[0] ?? null;
  if (!latest?.time) return { latest: null, expectedMs: null, due: false };

  let expectedMs = new Date(latest.time).getTime() + SIX_HOURS_MS;
  if (!Number.isFinite(expectedMs)) return { latest, expectedMs: null, due: false };

  // If a scheduled six-hour report is only a few minutes late, keep showing it
  // as DUE NOW instead of jumping ahead six hours. Once the report lands, the
  // latest six-hour row advances and this naturally rolls to the next cycle.
  while (expectedMs + DUE_GRACE_MS < nowMs) expectedMs += SIX_HOURS_MS;
  const due = nowMs >= expectedMs && nowMs - expectedMs <= DUE_GRACE_MS;
  return { latest, expectedMs, due };
}

function countdown(expectedMs: number | null, nowMs: number, due: boolean) {
  if (expectedMs === null) return "schedule learning";
  if (due) return "DUE NOW";
  const delta = Math.max(0, expectedMs - nowMs);
  const hours = Math.floor(delta / 3_600_000);
  const minutes = Math.floor((delta % 3_600_000) / 60_000);
  const seconds = Math.floor((delta % 60_000) / 1000);
  if (hours > 0) return `in ${hours}h ${minutes}m`;
  if (minutes > 0) return `in ${minutes}m ${seconds}s`;
  return `in ${seconds}s`;
}

export function SixHourReleaseStrip() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/weather-dashboard?sixhour=${Date.now()}`, {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache" },
      });
      const next = await response.json();
      if (!response.ok) throw new Error(next.error ?? "weather request failed");
      setData(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "unable to load");
    }
  }, []);

  const schedules = useMemo(
    () => data?.stations.map((station) => ({ station, ...scheduleFromLatest(station, nowMs) })) ?? [],
    [data, nowMs],
  );

  const rapid = schedules.some(({ expectedMs }) => expectedMs !== null && Math.abs(expectedMs - nowMs) <= 10 * 60 * 1000);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      await load();
      if (!cancelled) timer = window.setTimeout(tick, rapid ? 2_000 : 15_000);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [load, rapid]);

  return (
    <section style={{ background: "#071a14", color: "#f5fbf7", padding: "12px 12px 14px", borderBottom: "1px solid #244236" }}>
      <div style={{ maxWidth: 980, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 9, letterSpacing: ".15em", textTransform: "uppercase", color: "#9ecfb2", fontWeight: 800 }}>Six-hour ASOS release clock</div>
            <div style={{ marginTop: 3, fontSize: 17, fontWeight: 800, letterSpacing: "-.02em" }}>Next release + maxima exposed</div>
          </div>
          <div style={{ fontSize: 9, color: error ? "#ffb4aa" : rapid ? "#d7ff8a" : "#9eb2a8", textAlign: "right" }}>
            {error ? error : rapid ? "2s polling near release" : "15s polling"}
          </div>
        </div>

        <div style={{ display: "flex", gap: 9, overflowX: "auto", paddingBottom: 2, WebkitOverflowScrolling: "touch" }}>
          {schedules.map(({ station, latest, expectedMs, due }) => {
            const max = latest?.high6 ?? null;
            const min = latest?.low6 ?? null;
            const hiCode = maxCode(latest?.raw ?? null);
            const loCode = minCode(latest?.raw ?? null);
            const received = latest?.receivedAt ?? null;
            const expectedIso = expectedMs === null ? null : new Date(expectedMs).toISOString();
            return (
              <article key={station.stid} style={{ flex: "0 0 245px", background: due ? "#173a28" : "#10271e", border: due ? "1px solid #b7ef86" : "1px solid #29483b", borderRadius: 14, padding: 12 }}>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 10, color: "#94b4a4", letterSpacing: ".12em" }}>{station.stid}</div>
                    <strong style={{ display: "block", marginTop: 2, fontSize: 17 }}>{station.city}</strong>
                  </div>
                  <div style={{ fontSize: 10, fontWeight: 900, color: due ? "#d7ff8a" : "#b8d6c7", textAlign: "right" }}>{countdown(expectedMs, nowMs, due)}</div>
                </div>

                <div style={{ marginTop: 12, padding: "10px 11px", background: "#081b14", borderRadius: 10, border: "1px solid #244437" }}>
                  <div style={{ fontSize: 8, color: "#8fb2a1", letterSpacing: ".13em", textTransform: "uppercase", fontWeight: 800 }}>Max exposed by last 6h report</div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 3 }}>
                    <strong style={{ fontSize: 29, lineHeight: 1 }}>{fmtTemp(max)}</strong>
                    {hiCode && <code style={{ color: "#d7ff8a", fontSize: 12 }}>{hiCode}</code>}
                  </div>
                  <div style={{ marginTop: 6, fontSize: 10, color: "#9eb2a8" }}>
                    Min {fmtTemp(min)}{loCode ? ` · ${loCode}` : ""}
                  </div>
                </div>

                <div style={{ marginTop: 9, display: "grid", gap: 4, fontSize: 10, color: "#b6c8bf" }}>
                  <div><span style={{ color: "#78998a" }}>Last report:</span> {latest?.time ? fmtTime(latest.time, station.timezone) : "—"}</div>
                  <div><span style={{ color: "#78998a" }}>Received:</span> {received ? fmtTime(received, station.timezone, true) : latest ? `${latest.source ?? "source"} arrival not timestamped` : "—"}</div>
                  <div><span style={{ color: "#78998a" }}>Next expected:</span> {expectedIso ? fmtTime(expectedIso, station.timezone) : "learning from reports"}</div>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
