import { NextRequest, NextResponse } from "next/server";
import { authorized } from "@/lib/auth";
import { pool, transaction } from "@/lib/db";

export const dynamic = "force-dynamic";

const PAPER_EXECUTABLE_STRATEGIES = new Set(["DBN"]);

type ModePatch = {
  modeCode: string;
  enabled?: boolean;
  startingBankroll?: number;
  config?: Record<string, unknown>;
};

type StrategyPatch = {
  strategyCode: string;
  enabled?: boolean;
  paperTradeEnabled?: boolean;
  shadowEnabled?: boolean;
  config?: Record<string, unknown>;
};

type ConfigPatch = {
  global?: Record<string, unknown>;
  modes?: ModePatch[];
  strategies?: StrategyPatch[];
};

async function payload() {
  if (!pool) {
    return {
      available: false,
      global: null,
      modes: [],
      strategies: [],
      portfolios: [],
      latestSession: null,
    };
  }

  const [global, modes, strategies, session] = await Promise.all([
    pool.query<{ config: Record<string, unknown>; updated_at: Date }>(
      "SELECT config, updated_at FROM paper_global_config WHERE id=1",
    ),
    pool.query<{
      mode_code: string;
      display_name: string;
      description: string;
      enabled: boolean;
      starting_bankroll: string;
      config: Record<string, unknown>;
      updated_at: Date;
    }>(
      "SELECT mode_code, display_name, description, enabled, starting_bankroll::text, config, updated_at FROM paper_portfolio_mode_configs ORDER BY mode_code",
    ),
    pool.query<{
      strategy_code: string;
      display_name: string;
      family: string;
      enabled: boolean;
      paper_trade_enabled: boolean;
      shadow_enabled: boolean;
      real_money_eligible: boolean;
      config: Record<string, unknown>;
      updated_at: Date;
    }>(
      "SELECT strategy_code, display_name, family, enabled, paper_trade_enabled, shadow_enabled, real_money_eligible, config, updated_at FROM paper_strategy_configs ORDER BY strategy_code",
    ),
    pool.query<{ id: string; started_at: Date; status: string }>(
      "SELECT id, started_at, status FROM paper_sessions WHERE mode='paper_live' ORDER BY started_at DESC LIMIT 1",
    ),
  ]);

  const latestSession = session.rows[0] ?? null;
  const portfolios = latestSession
    ? await pool.query<{
        id: string;
        mode_code: string;
        starting_bankroll: string;
        cash_balance: string;
        reserved_cash: string;
        realized_pnl: string;
        status: string;
        created_at: Date;
        updated_at: Date;
      }>(
        `SELECT id::text, mode_code, starting_bankroll::text, cash_balance::text,
                reserved_cash::text, realized_pnl::text, status, created_at, updated_at
           FROM paper_portfolios
          WHERE session_id=$1
          ORDER BY mode_code`,
        [latestSession.id],
      )
    : { rows: [] };

  return {
    available: true,
    global: global.rows[0]
      ? { config: global.rows[0].config, updatedAt: global.rows[0].updated_at.toISOString() }
      : null,
    modes: modes.rows.map((row) => ({
      modeCode: row.mode_code,
      displayName: row.display_name,
      description: row.description,
      enabled: row.enabled,
      startingBankroll: Number(row.starting_bankroll),
      config: row.config,
      updatedAt: row.updated_at.toISOString(),
    })),
    strategies: strategies.rows.map((row) => ({
      strategyCode: row.strategy_code,
      displayName: row.display_name,
      family: row.family,
      enabled: row.enabled,
      paperTradeEnabled: row.paper_trade_enabled,
      shadowEnabled: row.shadow_enabled,
      realMoneyEligible: row.real_money_eligible,
      paperExecutable: PAPER_EXECUTABLE_STRATEGIES.has(row.strategy_code),
      config: row.config,
      updatedAt: row.updated_at.toISOString(),
    })),
    portfolios: portfolios.rows.map((row) => ({
      id: row.id,
      modeCode: row.mode_code,
      startingBankroll: Number(row.starting_bankroll),
      cashBalance: Number(row.cash_balance),
      reservedCash: Number(row.reserved_cash),
      realizedPnl: Number(row.realized_pnl),
      status: row.status,
      createdAt: row.created_at.toISOString(),
      updatedAt: row.updated_at.toISOString(),
    })),
    latestSession: latestSession
      ? { id: latestSession.id, startedAt: latestSession.started_at.toISOString(), status: latestSession.status }
      : null,
  };
}

export async function GET() {
  try {
    return NextResponse.json(await payload());
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "paper configuration unavailable" },
      { status: 500 },
    );
  }
}

export async function PUT(request: NextRequest) {
  if (!authorized(request)) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  if (!pool) return NextResponse.json({ error: "database unavailable" }, { status: 503 });

  const body = await request.json() as ConfigPatch;
  if (body.global && body.global.real_money_enabled === true) {
    return NextResponse.json(
      { error: "real-money execution cannot be enabled from the paper configuration endpoint" },
      { status: 400 },
    );
  }
  const unsupportedTrade = (body.strategies ?? []).find(
    (patch) => patch.paperTradeEnabled === true && !PAPER_EXECUTABLE_STRATEGIES.has(patch.strategyCode),
  );
  if (unsupportedTrade) {
    return NextResponse.json(
      { error: `${unsupportedTrade.strategyCode} does not have a validated paper execution engine yet; keep it shadow-only` },
      { status: 400 },
    );
  }

  await transaction(async (client) => {
    if (body.global) {
      const safeGlobal = { ...body.global };
      delete safeGlobal.real_money_enabled;
      if (Object.keys(safeGlobal).length) {
        await client.query(
          `UPDATE paper_global_config
              SET config = config || $1::jsonb, updated_at=now()
            WHERE id=1`,
          [JSON.stringify(safeGlobal)],
        );
      }
    }

    for (const patch of body.modes ?? []) {
      if (!patch.modeCode) continue;
      if (patch.startingBankroll !== undefined && (!Number.isFinite(patch.startingBankroll) || patch.startingBankroll <= 0)) {
        throw new Error(`invalid starting bankroll for ${patch.modeCode}`);
      }
      await client.query(
        `UPDATE paper_portfolio_mode_configs
            SET enabled=COALESCE($2, enabled),
                starting_bankroll=COALESCE($3, starting_bankroll),
                config=config || COALESCE($4::jsonb, '{}'::jsonb),
                updated_at=now()
          WHERE mode_code=$1`,
        [
          patch.modeCode,
          patch.enabled ?? null,
          patch.startingBankroll ?? null,
          patch.config ? JSON.stringify(patch.config) : null,
        ],
      );
    }

    for (const patch of body.strategies ?? []) {
      if (!patch.strategyCode) continue;
      const paperTradeEnabled = PAPER_EXECUTABLE_STRATEGIES.has(patch.strategyCode)
        ? patch.paperTradeEnabled ?? null
        : false;
      await client.query(
        `UPDATE paper_strategy_configs
            SET enabled=COALESCE($2, enabled),
                paper_trade_enabled=COALESCE($3, paper_trade_enabled),
                shadow_enabled=COALESCE($4, shadow_enabled),
                config=config || COALESCE($5::jsonb, '{}'::jsonb),
                updated_at=now()
          WHERE strategy_code=$1`,
        [
          patch.strategyCode,
          patch.enabled ?? null,
          paperTradeEnabled,
          patch.shadowEnabled ?? null,
          patch.config ? JSON.stringify(patch.config) : null,
        ],
      );
    }
  });

  return NextResponse.json(await payload());
}
