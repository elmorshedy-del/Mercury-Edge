"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./PaperTradingControl.module.css";

type Mode = {
  modeCode: string;
  displayName: string;
  description: string;
  enabled: boolean;
  startingBankroll: number;
  config: Record<string, unknown>;
  updatedAt: string;
};

type Strategy = {
  strategyCode: string;
  displayName: string;
  family: string;
  enabled: boolean;
  paperTradeEnabled: boolean;
  shadowEnabled: boolean;
  realMoneyEligible: boolean;
  config: Record<string, unknown>;
};

type Portfolio = {
  id: string;
  modeCode: string;
  startingBankroll: number;
  cashBalance: number;
  reservedCash: number;
  realizedPnl: number;
  status: string;
};

type Payload = {
  available: boolean;
  global: { config: Record<string, unknown>; updatedAt: string } | null;
  modes: Mode[];
  strategies: Strategy[];
  portfolios: Portfolio[];
  latestSession: { id: string; startedAt: string; status: string } | null;
};

type ModeDraft = {
  enabled: boolean;
  maxTradePct: number;
  maxEventPct: number;
  maxRegionPct: number;
  maxDailyPct: number;
  reservePct: number;
  executionLatencyMs: number;
};

function num(value: unknown, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function percent(value: number) {
  return Math.round(value * 1000) / 10;
}

function money(value: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(value);
}

export function PaperTradingControl() {
  const [data, setData] = useState<Payload | null>(null);
  const [bankroll, setBankroll] = useState(1000);
  const [token, setToken] = useState("");
  const [modeDrafts, setModeDrafts] = useState<Record<string, ModeDraft>>({});
  const [strategyDrafts, setStrategyDrafts] = useState<Record<string, Pick<Strategy, "enabled" | "paperTradeEnabled" | "shadowEnabled">>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const response = await fetch("/api/paper/config", { cache: "no-store" });
    if (!response.ok) throw new Error(`Paper config returned ${response.status}`);
    const next = await response.json() as Payload;
    setData(next);
    if (next.modes.length) setBankroll(next.modes[0].startingBankroll);
    setModeDrafts(Object.fromEntries(next.modes.map((mode) => [mode.modeCode, {
      enabled: mode.enabled,
      maxTradePct: num(mode.config.max_trade_pct, 0.2),
      maxEventPct: num(mode.config.max_event_pct, 0.25),
      maxRegionPct: num(mode.config.max_region_pct, 0.4),
      maxDailyPct: num(mode.config.max_daily_deployed_pct, 0.7),
      reservePct: num(mode.config.reserve_pct, 0.2),
      executionLatencyMs: num(mode.config.execution_latency_ms, 100),
    }])));
    setStrategyDrafts(Object.fromEntries(next.strategies.map((strategy) => [strategy.strategyCode, {
      enabled: strategy.enabled,
      paperTradeEnabled: strategy.paperTradeEnabled,
      shadowEnabled: strategy.shadowEnabled,
    }])));
  }, []);

  useEffect(() => {
    void load().catch((error) => setMessage(error instanceof Error ? error.message : "Unable to load paper controls"));
    const timer = window.setInterval(() => void load().catch(() => undefined), 10_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const portfolioMap = useMemo(
    () => new Map(data?.portfolios.map((portfolio) => [portfolio.modeCode, portfolio]) ?? []),
    [data],
  );

  async function save() {
    if (!data) return;
    if (!token.trim()) {
      setMessage("Enter the dashboard admin token before saving. It is never stored by this page.");
      return;
    }
    if (!Number.isFinite(bankroll) || bankroll <= 0) {
      setMessage("Bankroll per mode must be greater than zero.");
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const response = await fetch("/api/paper/config", {
        method: "PUT",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${token.trim()}`,
        },
        body: JSON.stringify({
          global: { starting_bankroll: bankroll, all_modes_same_bankroll: true },
          modes: data.modes.map((mode) => {
            const draft = modeDrafts[mode.modeCode];
            return {
              modeCode: mode.modeCode,
              enabled: draft?.enabled ?? mode.enabled,
              startingBankroll: bankroll,
              config: draft ? {
                max_trade_pct: draft.maxTradePct,
                max_event_pct: draft.maxEventPct,
                max_region_pct: draft.maxRegionPct,
                max_daily_deployed_pct: draft.maxDailyPct,
                reserve_pct: draft.reservePct,
                execution_latency_ms: draft.executionLatencyMs,
              } : {},
            };
          }),
          strategies: data.strategies.map((strategy) => {
            const draft = strategyDrafts[strategy.strategyCode];
            return {
              strategyCode: strategy.strategyCode,
              enabled: draft?.enabled ?? strategy.enabled,
              paperTradeEnabled: draft?.paperTradeEnabled ?? strategy.paperTradeEnabled,
              shadowEnabled: draft?.shadowEnabled ?? strategy.shadowEnabled,
            };
          }),
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error ?? `Save failed with ${response.status}`);
      setData(body as Payload);
      setMessage("Saved. New sessions and future signals will use these settings.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to save paper settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={styles.section} id="paper-trading">
      <div className={styles.shell}>
        <header className={styles.header}>
          <div>
            <span className={styles.kicker}>Parallel paper portfolios</span>
            <h2>Same $1,000. Same signals. Different capital rules.</h2>
            <p>
              Every enabled mode receives the identical audited signal stream and order-book timeline. Capital is not split between modes: each mode is an independent virtual portfolio so performance remains directly comparable.
            </p>
          </div>
          <div className={styles.status}>{data?.latestSession ? `Session ${data.latestSession.status}` : "Waiting for session"}</div>
        </header>

        <div className={styles.controls}>
          <div className={styles.controlCard}>
            <label>Bankroll per mode</label>
            <div className={styles.inputRow}>
              <input className={styles.input} type="number" min="1" step="50" value={bankroll} onChange={(event) => setBankroll(Number(event.target.value))} />
              <strong>each</strong>
            </div>
            <div className={styles.note}>Changing this applies the same starting bankroll to every mode. Current baseline: $1,000.</div>
          </div>
          <div className={styles.controlCard}>
            <label>Admin token · required only to save</label>
            <div className={styles.inputRow}>
              <input className={styles.input} type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Bearer token" autoComplete="off" />
              <button className={styles.button} onClick={() => void save()} disabled={saving || !data}>{saving ? "Saving…" : "Save all"}</button>
            </div>
            <div className={styles.note}>The token stays in this browser field only; it is not returned by the server or stored in page state after refresh.</div>
          </div>
        </div>

        <div className={styles.modeGrid}>
          {data?.modes.map((mode) => {
            const draft = modeDrafts[mode.modeCode];
            const portfolio = portfolioMap.get(mode.modeCode);
            const config = draft ?? {
              enabled: mode.enabled,
              maxTradePct: num(mode.config.max_trade_pct, 0),
              maxEventPct: num(mode.config.max_event_pct, 0),
              maxRegionPct: num(mode.config.max_region_pct, 0),
              maxDailyPct: num(mode.config.max_daily_deployed_pct, 0),
              reservePct: num(mode.config.reserve_pct, 0),
              executionLatencyMs: num(mode.config.execution_latency_ms, 100),
            };
            const deployed = portfolio ? Math.max(0, portfolio.startingBankroll - portfolio.cashBalance) : 0;
            return (
              <article className={styles.modeCard} key={mode.modeCode}>
                <div className={styles.modeTop}>
                  <div><h3>{mode.displayName}</h3><p>{mode.description}</p></div>
                  <span className={styles.badge}>{Boolean(mode.config.recommended) ? "baseline" : mode.modeCode}</span>
                </div>
                <div className={styles.money}>
                  <div><span>Start</span><strong>{money(portfolio?.startingBankroll ?? bankroll)}</strong></div>
                  <div><span>Cash</span><strong>{money(portfolio?.cashBalance ?? bankroll)}</strong></div>
                  <div><span>Deployed</span><strong>{money(deployed)}</strong></div>
                  <div><span>Realized P&L</span><strong>{money(portfolio?.realizedPnl ?? 0)}</strong></div>
                  <div><span>Reserved</span><strong>{money(portfolio?.reservedCash ?? 0)}</strong></div>
                  <div><span>Status</span><strong>{portfolio?.status ?? "pending"}</strong></div>
                </div>
                <div className={styles.riskGrid}>
                  <label>Max trade %<input type="number" min="0" max="1" step="0.01" value={config.maxTradePct} onChange={(e) => setModeDrafts((old) => ({ ...old, [mode.modeCode]: { ...config, maxTradePct: Number(e.target.value) } }))} /></label>
                  <label>Max event %<input type="number" min="0" max="1" step="0.01" value={config.maxEventPct} onChange={(e) => setModeDrafts((old) => ({ ...old, [mode.modeCode]: { ...config, maxEventPct: Number(e.target.value) } }))} /></label>
                  <label>Max region %<input type="number" min="0" max="1" step="0.01" value={config.maxRegionPct} onChange={(e) => setModeDrafts((old) => ({ ...old, [mode.modeCode]: { ...config, maxRegionPct: Number(e.target.value) } }))} /></label>
                  <label>Daily deploy %<input type="number" min="0" max="1" step="0.01" value={config.maxDailyPct} onChange={(e) => setModeDrafts((old) => ({ ...old, [mode.modeCode]: { ...config, maxDailyPct: Number(e.target.value) } }))} /></label>
                  <label>Reserve %<input type="number" min="0" max="1" step="0.01" value={config.reservePct} onChange={(e) => setModeDrafts((old) => ({ ...old, [mode.modeCode]: { ...config, reservePct: Number(e.target.value) } }))} /></label>
                  <label>Latency ms<input type="number" min="0" max="5000" step="10" value={config.executionLatencyMs} onChange={(e) => setModeDrafts((old) => ({ ...old, [mode.modeCode]: { ...config, executionLatencyMs: Number(e.target.value) } }))} /></label>
                </div>
                <div className={styles.checks} style={{ marginTop: 12 }}>
                  <label><input type="checkbox" checked={config.enabled} onChange={(e) => setModeDrafts((old) => ({ ...old, [mode.modeCode]: { ...config, enabled: e.target.checked } }))} /> enabled</label>
                  <span>{percent(config.maxDailyPct)}% max/day</span>
                </div>
              </article>
            );
          })}
        </div>

        <div className={styles.strategyPanel}>
          <h3>Strategy controls</h3>
          <div className={styles.strategyGrid}>
            {data?.strategies.map((strategy) => {
              const draft = strategyDrafts[strategy.strategyCode] ?? strategy;
              return (
                <div className={styles.strategy} key={strategy.strategyCode}>
                  <div><strong>{strategy.strategyCode} · {strategy.displayName}</strong><small>{strategy.family}{strategy.realMoneyEligible ? " · Phase-1 eligible" : " · research"}</small></div>
                  <div className={styles.checks}>
                    <label><input type="checkbox" checked={draft.paperTradeEnabled} onChange={(e) => setStrategyDrafts((old) => ({ ...old, [strategy.strategyCode]: { ...draft, paperTradeEnabled: e.target.checked } }))} /> trade</label>
                    <label><input type="checkbox" checked={draft.shadowEnabled} onChange={(e) => setStrategyDrafts((old) => ({ ...old, [strategy.strategyCode]: { ...draft, shadowEnabled: e.target.checked } }))} /> shadow</label>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <footer className={styles.footer}>
          <span>Real-money order submission is hard-disabled in this service. These controls affect paper portfolios only.</span>
          <span className={message?.toLowerCase().includes("saved") ? styles.success : message ? styles.error : ""}>{message ?? (data?.available ? "Configuration loaded" : "Paper tables not available yet")}</span>
        </footer>
      </div>
    </section>
  );
}
