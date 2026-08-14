"use client";

import { useCallback, useEffect, useState } from "react";
import { STATIONS } from "@/lib/config";
import type { JobControlAction, ResearchJob, ResearchJobStatus } from "@/lib/jobs/types";
import { Alert, Check, Database } from "./Icons";

type ResultsPayload = {
  generatedAt?: string;
  latestRun: null | {
    id: number;
    modelVersion: string;
    finishedAt: string | null;
    summary: Record<string, unknown>;
  };
  stations: Array<{
    station: string;
    signals: number;
    executable: number;
    resolved: number;
    wins: number;
    winRate: number | null;
    averageLagSeconds: number | null;
    grossProfit: number | null;
    netProfit: number | null;
  }>;
  coverage: Array<{
    station: string;
    firstObservation: string | null;
    lastObservation: string | null;
    observations: number;
    actualReceipts: number;
    discoveryOnly: number;
    marketDays: number;
    quotes: number;
  }>;
};

type JobDetail = {
  job: ResearchJob;
  items: Array<{
    id: number; itemKey: string; station: string | null; date: string | null;
    status: string; attempts: number; observations: number; contracts: number;
    quotes: number; errorMessage: string | null;
  }>;
  events: Array<{
    id: number; level: string; message: string; createdAt: string;
  }>;
};

const ACTIVE = new Set<ResearchJobStatus>(["blocked", "queued", "running", "pause_requested", "paused", "cancel_requested"]);

function isoDate(date: Date) {
  return date.toISOString().slice(0, 10);
}

function defaultRange() {
  const to = new Date();
  to.setUTCDate(to.getUTCDate() - 1);
  const from = new Date(to);
  from.setUTCDate(from.getUTCDate() - 13);
  return { from: isoDate(from), to: isoDate(to) };
}

function fmtNumber(value: unknown, digits = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toLocaleString(undefined, { maximumFractionDigits: digits }) : "—";
}

function fmtSeconds(value: number | null) {
  if (value === null) return "—";
  if (value < 60) return `${Math.round(value)}s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

function fmtTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function allowedActions(status: ResearchJobStatus): JobControlAction[] {
  if (status === "running" || status === "queued") return ["pause", "cancel"];
  if (status === "pause_requested" || status === "cancel_requested") return [];
  if (status === "paused") return ["resume", "cancel"];
  if (["failed", "cancelled", "succeeded_with_warnings"].includes(status)) return ["retry"];
  return [];
}

export function ResearchControl() {
  const [range] = useState(() => defaultRange());
  const [from, setFrom] = useState(range.from);
  const [to, setTo] = useState(range.to);
  const [stations, setStations] = useState<string[]>(["KNYC", "KPHL"]);
  const [jobs, setJobs] = useState<ResearchJob[]>([]);
  const [results, setResults] = useState<ResultsPayload>({ latestRun: null, stations: [], coverage: [] });
  const [tokenInput, setTokenInput] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<JobDetail | null>(null);

  const refresh = useCallback(async () => {
    const [jobsResponse, resultsResponse] = await Promise.all([
      fetch("/api/jobs?limit=30", { cache: "no-store" }),
      fetch("/api/results", { cache: "no-store" }),
    ]);
    if (jobsResponse.ok) setJobs((await jobsResponse.json()).jobs ?? []);
    if (resultsResponse.ok) setResults(await resultsResponse.json());
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => {
      const saved = sessionStorage.getItem("mercury-control-token");
      if (saved) setToken(saved);
      void refresh();
    }, 0);
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    const load = async () => {
      const response = await fetch(`/api/jobs/${expanded}`, { cache: "no-store" });
      if (response.ok && !cancelled) setDetail(await response.json());
    };
    void load();
    const timer = window.setInterval(() => void load(), 5_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [expanded]);

  async function unlock() {
    setBusy(true); setError(null);
    const response = await fetch("/api/control/verify", {
      method: "POST",
      headers: { Authorization: `Bearer ${tokenInput}` },
    });
    if (response.ok) {
      sessionStorage.setItem("mercury-control-token", tokenInput);
      setToken(tokenInput); setTokenInput(""); setNotice("Controls unlocked for this browser tab.");
    } else setError("That control key was not accepted.");
    setBusy(false);
  }

  function lock() {
    sessionStorage.removeItem("mercury-control-token");
    setToken(""); setNotice(null);
  }

  async function queue(jobType: "pipeline" | "backfill" | "backtest") {
    if (!stations.length) { setError("Select at least one station."); return; }
    setBusy(true); setError(null); setNotice(null);
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ jobType, from, to, stations }),
    });
    const payload = await response.json();
    if (!response.ok) setError(payload.error ?? "The job could not be queued.");
    else {
      setNotice(jobType === "pipeline" ? "Backfill queued; its backtest unlocks only after every city-day completes without unresolved failures." : `${jobType} queued.`);
      await refresh();
    }
    setBusy(false);
  }

  async function act(jobId: string, action: JobControlAction) {
    setBusy(true); setError(null);
    const response = await fetch(`/api/jobs/${jobId}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ action }),
    });
    const payload = await response.json();
    if (!response.ok) setError(payload.error ?? `Could not ${action} job.`);
    else setNotice(`Job ${action} accepted.`);
    await refresh();
    setBusy(false);
  }

  function stationPreset(value: "core" | "all") {
    setStations(value === "core" ? ["KNYC", "KPHL"] : STATIONS.map((station) => station.station));
  }

  const activeJobs = jobs.filter((job) => ACTIVE.has(job.status)).length;
  const latestSummary = results.latestRun?.summary ?? {};
  const unlocked = Boolean(token);

  return (
    <section className="controlSection" id="control">
      <div className="shell">
        <div className="controlHeader">
          <div><span className="kicker">Research control plane</span><h2>Run it here.<br />Audit every step.</h2></div>
          <div className="controlHeartbeat"><i className={activeJobs ? "active" : ""} /><span>{activeJobs ? `${activeJobs} active job${activeJobs === 1 ? "" : "s"}` : "Worker standing by"}</span><small>refreshes every 5 seconds</small></div>
        </div>

        <div className="controlGrid">
          <aside className="jobComposer">
            <div className="composerTitle"><Database /><div><strong>New research run</strong><span>Nothing starts until you press Queue.</span></div></div>
            <div className="fieldRow"><label>From<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label><label>Through<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label></div>
            <div className="stationPicker">
              <div className="fieldLabel"><span>Official stations</span><div><button onClick={() => stationPreset("core")}>NYC + PHL</button><button onClick={() => stationPreset("all")}>All 20</button></div></div>
              <div className="stationChips">{STATIONS.map((station) => {
                const selected = stations.includes(station.station);
                return <button className={selected ? "selected" : ""} key={station.station} onClick={() => setStations(selected ? stations.filter((item) => item !== station.station) : [...stations, station.station])}><b>{station.station}</b><small>{station.city}</small></button>;
              })}</div>
            </div>
            {!unlocked ? <div className="unlockBox"><label>Railway control key<input type="password" value={tokenInput} onChange={(event) => setTokenInput(event.target.value)} placeholder="INGEST_TOKEN" onKeyDown={(event) => { if (event.key === "Enter") void unlock(); }} /></label><button onClick={() => void unlock()} disabled={busy || !tokenInput}>Unlock controls</button><small>Held only in this browser tab; never written to the database.</small></div> : <div className="unlockedBox"><span><Check />Controls unlocked</span><button onClick={lock}>Lock</button></div>}
            <div className="queueActions">
              <button className="queuePrimary" disabled={!unlocked || busy} onClick={() => void queue("pipeline")}><strong>Backfill + backtest</strong><small>Recommended pipeline</small></button>
              <button disabled={!unlocked || busy} onClick={() => void queue("backfill")}><strong>Backfill only</strong><small>Store source data</small></button>
              <button disabled={!unlocked || busy} onClick={() => void queue("backtest")}><strong>Backtest stored data</strong><small>No new downloads</small></button>
            </div>
            {notice && <p className="controlNotice success"><Check />{notice}</p>}
            {error && <p className="controlNotice error"><Alert />{error}</p>}
          </aside>

          <div className="jobMonitor">
            <div className="monitorHeader"><div><span>Persistent queue</span><h3>Runs &amp; progress</h3></div><small>{jobs.length} recorded</small></div>
            <div className="jobsList">{jobs.slice(0, 12).map((job) => {
              const progress = job.progressTotal ? Math.min(100, (job.progressCurrent / job.progressTotal) * 100) : 0;
              return <div className={`jobRow status-${job.status}`} key={job.id}>
                <button className="jobSummary" onClick={() => { setDetail(null); setExpanded(expanded === job.id ? null : job.id); }}>
                  <span className="jobType">{job.jobType}</span>
                  <span className="jobScope"><b>{job.parameters.stations.join(", ")}</b><small>{job.parameters.from} → {job.parameters.to}</small></span>
                  <span className={`jobStatus ${job.status}`}>{job.status.replaceAll("_", " ")}</span>
                  <span className="jobProgress"><i><b style={{ width: `${progress}%` }} /></i><small>{job.progressCurrent}/{job.progressTotal || "—"}</small></span>
                  <span className="jobChevron">{expanded === job.id ? "−" : "+"}</span>
                </button>
                {expanded === job.id && <div className="jobAudit">
                  <div className="jobAuditTop"><span>Created {fmtTime(job.createdAt)}</span><span>Heartbeat {fmtTime(job.heartbeatAt)}</span><div>{allowedActions(job.status).map((action) => <button key={action} disabled={!unlocked || busy} onClick={() => void act(job.id, action)}>{action}</button>)}</div></div>
                  {job.errorMessage && <p className="auditError"><Alert />{job.errorMessage}</p>}
                  <div className="auditEvents">{detail?.job.id === job.id ? detail.events.slice(0, 12).map((event) => <div key={event.id}><i className={event.level} /><time>{fmtTime(event.createdAt)}</time><span>{event.message}</span></div>) : <span>Loading audit trail…</span>}</div>
                  {detail?.job.id === job.id && detail.items.length > 0 && <div className="itemSummary"><span>{detail.items.filter((item) => item.status === "succeeded").length} complete</span><span>{detail.items.filter((item) => item.status === "failed").length} failed</span><span>{detail.items.reduce((sum, item) => sum + item.observations, 0).toLocaleString()} observations</span><span>{detail.items.reduce((sum, item) => sum + item.quotes, 0).toLocaleString()} quotes processed</span></div>}
                </div>}
              </div>;
            })}{!jobs.length && <div className="emptyJobs"><Database /><strong>No database-controlled jobs yet</strong><span>Configure a range, unlock controls, then queue the first pipeline.</span></div>}</div>
          </div>
        </div>

        <div className="resultsWorkbench">
          <div className="resultsHeader"><div><span>Latest completed model run</span><h3>Backtest results</h3></div><small>{results.latestRun ? `${results.latestRun.modelVersion} · ${fmtTime(results.latestRun.finishedAt)}` : "Waiting for first controlled run"}</small></div>
          <div className="resultMetrics">
            <div><span>Events evaluated</span><strong>{fmtNumber(latestSummary.events)}</strong></div>
            <div><span>Signals</span><strong>{fmtNumber(latestSummary.signals)}</strong></div>
            <div><span>Executable proxies</span><strong>{fmtNumber(latestSummary.executableSignals)}</strong></div>
            <div><span>Win rate</span><strong>{latestSummary.winRate === null || latestSummary.winRate === undefined ? "—" : `${fmtNumber(Number(latestSummary.winRate) * 100, 1)}%`}</strong></div>
            <div><span>Net / one-contract rule</span><strong>{latestSummary.netProfitPerOneContract === undefined ? "—" : `$${fmtNumber(latestSummary.netProfitPerOneContract, 2)}`}</strong></div>
          </div>
          <div className="resultsTables">
            <div><h4>Station performance</h4><div className="miniTable"><div className="miniHead"><span>Station</span><span>Signals</span><span>Executable</span><span>Win rate</span><span>Avg lag</span><span>Net</span></div>{results.stations.map((row) => <div key={row.station}><span><b>{row.station}</b></span><span>{row.signals}</span><span>{row.executable}</span><span>{row.winRate === null ? "—" : `${fmtNumber(row.winRate * 100, 1)}%`}</span><span>{fmtSeconds(row.averageLagSeconds)}</span><span>{row.netProfit === null ? "—" : `$${fmtNumber(row.netProfit, 2)}`}</span></div>)}{!results.stations.length && <p>No completed signal set yet.</p>}</div></div>
            <div><h4>Stored-data coverage</h4><div className="miniTable coverageMini"><div className="miniHead"><span>Station</span><span>Market days</span><span>Observations</span><span>Actual receipts</span><span>Quotes</span></div>{results.coverage.map((row) => <div key={row.station}><span><b>{row.station}</b></span><span>{row.marketDays}</span><span>{row.observations.toLocaleString()}</span><span>{row.actualReceipts.toLocaleString()}</span><span>{row.quotes.toLocaleString()}</span></div>)}{!results.coverage.length && <p>Coverage appears after ingestion writes its first records.</p>}</div></div>
          </div>
          <p className="resultsCaveat"><Alert /> Backtest outputs remain quote proxies. The audit keeps original receipt quality, failed items, retries, fees, and non-executable evidence visible.</p>
        </div>
      </div>
    </section>
  );
}
