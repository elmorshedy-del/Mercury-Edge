"use client";

import type { CSSProperties } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./PaperTradingTerminal.module.css";

type Portfolio = {
  id: string;
  modeCode: string;
  startingBankroll: number;
  cashBalance: number;
  reservedCash: number;
  realizedPnl: number;
  status: string;
  createdAt?: string;
  updatedAt?: string;
};

type Mode = {
  modeCode: string;
  displayName: string;
  description: string;
  enabled: boolean;
  startingBankroll: number;
  config: Record<string, unknown>;
};

type ConfigPayload = {
  available: boolean;
  modes: Mode[];
  portfolios: Portfolio[];
  latestSession: { id: string; startedAt: string; status: string } | null;
};

type LiveSignal = {
  station: string;
  city: string;
  timezone: string;
  eventTicker: string;
  contractTicker: string;
  label: string;
  triggeredAt: string;
  triggerTemperatureF: number;
  entryYesAskCents: number | null;
  noAskProxyCents: number | null;
  latestYesAskCents: number | null;
  reactionLagSeconds: number | null;
  status: "open_gap" | "repriced" | "awaiting_quote";
};

type LivePayload = {
  generatedAt: string;
  signals: LiveSignal[];
};

function money(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function cents(value: number | null) {
  return value === null ? "—" : `${Math.round(value)}¢`;
}

function clock(value: string, timezone = "America/New_York") {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function age(value: string | null, now: number) {
  if (!value) return "—";
  const seconds = Math.max(0, Math.round((now - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m ago`;
}

function lag(value: number | null) {
  if (value === null) return "—";
  if (value < 60) return `${value}s`;
  return `${Math.floor(value / 60)}m ${value % 60}s`;
}

export function PaperTradingTerminal() {
  const [config, setConfig] = useState<ConfigPayload | null>(null);
  const [live, setLive] = useState<LivePayload | null>(null);
  const [selectedMode, setSelectedMode] = useState("balanced");
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const refresh = useCallback(async () => {
    try {
      const [configResponse, liveResponse] = await Promise.all([
        fetch("/api/paper/config", { cache: "no-store" }),
        fetch("/api/live", { cache: "no-store" }),
      ]);
      if (!configResponse.ok) throw new Error(`Paper config returned ${configResponse.status}`);
      if (!liveResponse.ok) throw new Error(`Live feed returned ${liveResponse.status}`);
      const nextConfig = await configResponse.json() as ConfigPayload;
      const nextLive = await liveResponse.json() as LivePayload;
      setConfig(nextConfig);
      setLive(nextLive);
      setNow(Date.now());
      setError(null);
      if (nextConfig.portfolios.length && !nextConfig.portfolios.some((portfolio) => portfolio.modeCode === selectedMode)) {
        setSelectedMode(nextConfig.portfolios[0].modeCode);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Terminal data unavailable");
    }
  }, [selectedMode]);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const portfolioByMode = useMemo(
    () => new Map(config?.portfolios.map((portfolio) => [portfolio.modeCode, portfolio]) ?? []),
    [config],
  );

  const selected = portfolioByMode.get(selectedMode) ?? config?.portfolios[0] ?? null;
  const selectedModeInfo = config?.modes.find((mode) => mode.modeCode === selected?.modeCode) ?? null;
  const start = selected?.startingBankroll ?? selectedModeInfo?.startingBankroll ?? 1000;
  const cash = selected?.cashBalance ?? start;
  const realized = selected?.realizedPnl ?? 0;
  const reserved = selected?.reservedCash ?? 0;
  const deployed = Math.max(0, start - cash);
  const utilization = start > 0 ? Math.min(100, (deployed / start) * 100) : 0;

  const maxAbsPnl = Math.max(1, ...(config?.portfolios.map((portfolio) => Math.abs(portfolio.realizedPnl)) ?? [1]));
  const signals = useMemo(
    () => [...(live?.signals ?? [])].sort((a, b) => new Date(b.triggeredAt).getTime() - new Date(a.triggeredAt).getTime()).slice(0, 10),
    [live],
  );

  const openSignals = live?.signals.filter((signal) => signal.status === "open_gap").length ?? 0;
  const repricedSignals = live?.signals.filter((signal) => signal.status === "repriced").length ?? 0;

  return (
    <section className={styles.terminal}>
      <header className={styles.topbar}>
        <div className={styles.brandBlock}>
          <a href="/" className={styles.brand}>
            <span className={styles.logoMark}>M</span>
            <span><b>Mercury Edge</b><small>Paper Terminal</small></span>
          </a>
          <span className={styles.paperBadge}>PAPER</span>
        </div>
        <div className={styles.topStatus}>
          <span className={styles.liveStatus}><i />{error ? "FEED ISSUE" : "LIVE"}</span>
          <span>Session {config?.latestSession?.status ?? "waiting"}</span>
          <span>{live ? age(live.generatedAt, now) : "connecting"}</span>
          <a href="/" className={styles.researchLink}>Research ↗</a>
        </div>
      </header>

      {error && <div className={styles.errorBar}>{error}. Trading logic and paper execution are unaffected.</div>}

      <div className={styles.workspace}>
        <aside className={styles.sidebar}>
          <div className={styles.sideLabel}>Portfolios</div>
          <div className={styles.portfolioList}>
            {config?.modes.filter((mode) => mode.enabled).map((mode) => {
              const portfolio = portfolioByMode.get(mode.modeCode);
              const active = (selected?.modeCode ?? selectedMode) === mode.modeCode;
              const pnl = portfolio?.realizedPnl ?? 0;
              return (
                <button
                  key={mode.modeCode}
                  type="button"
                  className={`${styles.portfolioButton} ${active ? styles.portfolioActive : ""}`}
                  onClick={() => setSelectedMode(mode.modeCode)}
                >
                  <span><b>{mode.displayName}</b><small>{mode.modeCode}</small></span>
                  <strong className={pnl > 0 ? styles.positive : pnl < 0 ? styles.negative : ""}>{money(pnl)}</strong>
                </button>
              );
            })}
            {!config?.modes.length && <div className={styles.sideEmpty}>Waiting for portfolios…</div>}
          </div>

          <div className={styles.sideSection}>
            <div className={styles.sideLabel}>Signal state</div>
            <div className={styles.sideStat}><span>Open gaps</span><b>{openSignals}</b></div>
            <div className={styles.sideStat}><span>Repriced</span><b>{repricedSignals}</b></div>
            <div className={styles.sideStat}><span>Total today</span><b>{live?.signals.length ?? 0}</b></div>
          </div>

          <div className={styles.safetyCard}>
            <span className={styles.safetyDot} />
            <div><b>Real money disabled</b><small>Display layer only. No order controls exist here.</small></div>
          </div>
        </aside>

        <main className={styles.main}>
          <section className={styles.accountHeader}>
            <div>
              <span className={styles.eyebrow}>Selected portfolio</span>
              <h1>{selectedModeInfo?.displayName ?? selected?.modeCode ?? "Paper portfolio"}</h1>
              <p>{selectedModeInfo?.description ?? "Live paper execution account."}</p>
            </div>
            <div className={styles.accountValue}>
              <span>Realized P&amp;L</span>
              <strong className={realized > 0 ? styles.positive : realized < 0 ? styles.negative : ""}>{money(realized)}</strong>
              <small>Starting bankroll {money(start)}</small>
            </div>
          </section>

          <section className={styles.metrics}>
            <article><span>Cash</span><strong>{money(cash)}</strong><small>available balance</small></article>
            <article><span>Capital deployed</span><strong>{money(deployed)}</strong><small>{utilization.toFixed(1)}% of bankroll</small></article>
            <article><span>Reserved</span><strong>{money(reserved)}</strong><small>held by paper engine</small></article>
            <article><span>Session</span><strong>{selected?.status ?? config?.latestSession?.status ?? "waiting"}</strong><small>{config?.latestSession ? age(config.latestSession.startedAt, now) : "not started"}</small></article>
          </section>

          <section className={styles.primaryGrid}>
            <article className={styles.chartCard}>
              <div className={styles.cardHeader}>
                <div><span>P&amp;L comparison</span><h2>Portfolio performance</h2></div>
                <small>Realized only · current session</small>
              </div>
              <div className={styles.pnlChart}>
                <div className={styles.zeroLine} />
                {config?.modes.filter((mode) => mode.enabled).map((mode) => {
                  const pnl = portfolioByMode.get(mode.modeCode)?.realizedPnl ?? 0;
                  const width = Math.min(48, (Math.abs(pnl) / maxAbsPnl) * 48);
                  return (
                    <div className={styles.pnlRow} key={mode.modeCode}>
                      <div className={styles.pnlName}><b>{mode.displayName}</b><small>{mode.modeCode}</small></div>
                      <div className={styles.pnlTrack}>
                        <span className={styles.centerTick} />
                        {pnl >= 0 ? (
                          <span className={`${styles.pnlBar} ${styles.pnlPositive}`} style={{ left: "50%", width: `${width}%` }} />
                        ) : (
                          <span className={`${styles.pnlBar} ${styles.pnlNegative}`} style={{ right: "50%", width: `${width}%` }} />
                        )}
                      </div>
                      <strong className={pnl > 0 ? styles.positive : pnl < 0 ? styles.negative : ""}>{money(pnl)}</strong>
                    </div>
                  );
                })}
                {!config?.portfolios.length && <div className={styles.chartEmpty}>P&amp;L will populate from existing portfolio balances when the paper session starts.</div>}
              </div>
            </article>

            <article className={styles.utilCard}>
              <div className={styles.cardHeader}>
                <div><span>Capital</span><h2>Utilization</h2></div>
                <small>{utilization.toFixed(1)}%</small>
              </div>
              <div className={styles.utilRing} style={{ "--utilization": `${utilization * 3.6}deg` } as CSSProperties}>
                <div><strong>{utilization.toFixed(0)}%</strong><span>deployed</span></div>
              </div>
              <div className={styles.utilRows}>
                <div><span>Starting</span><b>{money(start)}</b></div>
                <div><span>Cash</span><b>{money(cash)}</b></div>
                <div><span>Reserved</span><b>{money(reserved)}</b></div>
              </div>
            </article>
          </section>

          <section className={styles.tableCard}>
            <div className={styles.cardHeader}>
              <div><span>Live market intelligence</span><h2>Signal tape</h2></div>
              <small>Signal ≠ fill · existing /api/live feed</small>
            </div>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead><tr><th>Time</th><th>Station</th><th>Contract</th><th>Signal</th><th>NO entry proxy</th><th>Latest YES ask</th><th>Reaction</th><th>Status</th></tr></thead>
                <tbody>
                  {signals.map((signal) => (
                    <tr key={`${signal.station}-${signal.contractTicker}-${signal.triggeredAt}`}>
                      <td>{clock(signal.triggeredAt, signal.timezone)}</td>
                      <td><b>{signal.station}</b><small>{signal.city}</small></td>
                      <td><b>{signal.label}</b><small>{signal.contractTicker}</small></td>
                      <td><span className={styles.signalSide}>NO</span><small>{signal.triggerTemperatureF.toFixed(1)}°F trigger</small></td>
                      <td>{cents(signal.noAskProxyCents)}</td>
                      <td>{cents(signal.latestYesAskCents)}</td>
                      <td>{lag(signal.reactionLagSeconds)}</td>
                      <td><span className={`${styles.statusBadge} ${styles[signal.status]}`}>{signal.status.replace("_", " ")}</span></td>
                    </tr>
                  ))}
                  {!signals.length && (
                    <tr><td colSpan={8} className={styles.emptyRow}>No live signals in the current read-only feed yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className={styles.tableCard}>
            <div className={styles.cardHeader}>
              <div><span>Execution</span><h2>Trade ledger</h2></div>
              <small>Never inferred from signal data</small>
            </div>
            <div className={styles.ledgerHead}><span>Time</span><span>Market</span><span>Side</span><span>In</span><span>Out</span><span>Qty</span><span>P&amp;L</span></div>
            <div className={styles.ledgerEmpty}>
              <b>No trade-history read endpoint is currently exposed to the UI.</b>
              <span>This terminal intentionally leaves fills blank instead of presenting signals or quote proxies as executed trades.</span>
            </div>
          </section>
        </main>
      </div>
    </section>
  );
}
