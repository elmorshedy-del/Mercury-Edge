"use client";

import { useCallback, useEffect, useState } from "react";

type DsmRelease = {
  productId: string;
  issuedAt: string;
  summaryDate: string | null;
  cycle: string | null;
  reportedHighF: number | null;
  highObservedAt: string | null;
  highObservedClock: string | null;
  rawText: string;
  sourceUrl: string;
  transmissionCount: number;
};

type DsmStation = {
  stid: string;
  city: string;
  timezone: string;
  releases: DsmRelease[];
  error: string | null;
};

type DsmData = {
  updatedAt: string;
  source: string;
  pollFloorMs: number;
  stations: DsmStation[];
};

function fmtTemp(value: number | null) {
  return value === null ? "—" : `${value.toFixed(0)}°F`;
}

function fmtTime(iso: string | null, timezone: string) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
      timeZone: timezone,
    }).format(new Date(iso));
  } catch {
    return "—";
  }
}

function rawLine(text: string) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => /\bDS\b/.test(line)) ?? text.trim().slice(0, 180);
}

function statusFor(station: DsmStation, latest: DsmRelease | null) {
  if (station.error) return { label: "DEGRADED", color: "#ffb4aa" };
  if (!latest) return { label: "NO DSM", color: "#9fb1c1" };
  if (latest.reportedHighF === null) return { label: "NO MAX", color: "#ffd39a" };
  return { label: "MAX FOUND", color: "#a9d2f2" };
}

export function DsmReleaseStrip() {
  const [data, setData] = useState<DsmData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/weather-dashboard/dsm?ts=${Date.now()}`, {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache" },
      });
      const next = await response.json();
      if (!response.ok) throw new Error(next.error ?? "DSM request failed");
      setData(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load DSM feed");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      await load();
      if (!cancelled) timer = window.setTimeout(tick, 15_000);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [load]);

  return (
    <section style={{ background: "#0d151d", color: "#f4f7fa", padding: "12px 12px 14px", borderBottom: "1px solid #273746" }}>
      <div style={{ maxWidth: 980, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 9, letterSpacing: ".15em", textTransform: "uppercase", color: "#8fb6d7", fontWeight: 800 }}>DSM maximum check</div>
            <div style={{ marginTop: 3, fontSize: 17, fontWeight: 800, letterSpacing: "-.02em" }}>Daily maximum + occurrence time found inside DSM</div>
          </div>
          <div style={{ fontSize: 9, color: error ? "#ffb4aa" : "#9fb1c1", textAlign: "right" }}>
            {error ? error : "15s delivery polling · backup transmissions collapsed"}
          </div>
        </div>

        <div style={{ display: "flex", gap: 9, overflowX: "auto", paddingBottom: 2, WebkitOverflowScrolling: "touch" }}>
          {data?.stations.map((station) => {
            const latest = station.releases.find((release) => release.reportedHighF !== null) ?? station.releases[0] ?? null;
            const status = statusFor(station, latest);
            return (
              <article key={station.stid} style={{ flex: "0 0 245px", background: "#14212c", border: "1px solid #304657", borderRadius: 14, padding: 12 }}>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 10, color: "#8ba6bb", letterSpacing: ".12em" }}>{station.stid}</div>
                    <strong style={{ display: "block", marginTop: 2, fontSize: 17 }}>{station.city}</strong>
                  </div>
                  <div style={{ fontSize: 9, fontWeight: 800, color: status.color, textAlign: "right" }}>
                    {status.label}
                  </div>
                </div>

                <div style={{ marginTop: 12, padding: "10px 11px", background: "#0a1219", borderRadius: 10, border: "1px solid #263c4d" }}>
                  <div style={{ fontSize: 8, color: "#86a6bd", letterSpacing: ".13em", textTransform: "uppercase", fontWeight: 800 }}>Maximum reported in DSM</div>
                  <strong style={{ display: "block", marginTop: 3, fontSize: 29, lineHeight: 1 }}>{fmtTemp(latest?.reportedHighF ?? null)}</strong>
                  <div style={{ marginTop: 6, fontSize: 10, color: "#9fb1c1" }}>
                    High occurred {latest?.highObservedClock ?? "—"}
                  </div>
                </div>

                <div style={{ marginTop: 9, display: "grid", gap: 4, fontSize: 10, color: "#bdc9d2" }}>
                  <div><span style={{ color: "#7f98aa" }}>Issued:</span> {fmtTime(latest?.issuedAt ?? null, station.timezone)}</div>
                  {latest && latest.transmissionCount > 1 && (
                    <div><span style={{ color: "#7f98aa" }}>Delivery:</span> {latest.transmissionCount} transmissions collapsed</div>
                  )}
                </div>

                {latest?.rawText && (
                  <details style={{ marginTop: 9 }}>
                    <summary style={{ cursor: "pointer", fontSize: 10, color: "#9fc7e7" }}>Raw DSM</summary>
                    <code style={{ display: "block", marginTop: 6, whiteSpace: "pre-wrap", fontSize: 9, color: "#c7d4de" }}>{rawLine(latest.rawText)}</code>
                  </details>
                )}
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
