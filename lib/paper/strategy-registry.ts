export type StrategyCode =
  | "DBN"
  | "DSN"
  | "SBK"
  | "HSR"
  | "WTY"
  | "RMO"
  | "PRV"
  | "LVP"
  | "HMF";

export type StrategyFamily =
  | "hard_state"
  | "probability_transfer"
  | "lax_precision"
  | "sequential_lax";

export type StrategySpec = {
  code: StrategyCode;
  name: string;
  family: StrategyFamily;
  hardStateRequired: boolean;
  allowRealMoneyWhenProven: boolean;
  description: string;
  requiredEvidence: string[];
};

export const STRATEGIES: Record<StrategyCode, StrategySpec> = {
  DBN: {
    code: "DBN",
    name: "Dead-Bucket NO",
    family: "hard_state",
    hardStateRequired: true,
    allowRealMoneyWhenProven: true,
    description: "Buy NO on one bucket whose upper bound is below an irreversible settlement-compatible running maximum.",
    requiredEvidence: ["proven_settlement_compatibility", "exact_contract_bounds", "fresh_sequenced_l2_book", "verified_fees"],
  },
  DSN: {
    code: "DSN",
    name: "Dead-Set NO",
    family: "hard_state",
    hardStateRequired: true,
    allowRealMoneyWhenProven: false,
    description: "Buy NO across a set of eliminated buckets when routing or depth makes a multi-leg dead-set preferable.",
    requiredEvidence: ["proven_settlement_compatibility", "complete_dead_set", "fresh_sequenced_l2_books", "multi_leg_fill_risk_validated"],
  },
  SBK: {
    code: "SBK",
    name: "Survivor Basket",
    family: "hard_state",
    hardStateRequired: true,
    allowRealMoneyWhenProven: false,
    description: "Buy YES on every still-feasible mutually exclusive bucket when the exhaustive basket costs less than its certain payout after fees.",
    requiredEvidence: ["proven_settlement_compatibility", "exhaustive_survivor_set", "simultaneous_l2_depth", "verified_fees", "incomplete_basket_risk_validated"],
  },
  HSR: {
    code: "HSR",
    name: "Hard-State Router",
    family: "hard_state",
    hardStateRequired: true,
    allowRealMoneyWhenProven: false,
    description: "Compare payoff-equivalent DBN/DSN/SBK constructions and choose the cheapest sufficiently deep executable route.",
    requiredEvidence: ["proven_settlement_compatibility", "complete_event_books", "verified_fees", "atomicity_or_fill_risk_model"],
  },
  WTY: {
    code: "WTY",
    name: "Winner Transfer",
    family: "probability_transfer",
    hardStateRequired: false,
    allowRealMoneyWhenProven: false,
    description: "Buy YES on the bucket most likely to inherit probability after a new lower bound is established; retains higher-bucket forecast risk.",
    requiredEvidence: ["fresh_weather_state", "fresh_l2_book", "future_rise_model"],
  },
  RMO: {
    code: "RMO",
    name: "Rounding Momentum",
    family: "lax_precision",
    hardStateRequired: false,
    allowRealMoneyWhenProven: false,
    description: "Trade with the initial market move induced by a coarse high-frequency temperature print.",
    requiredEvidence: ["capturable_high_frequency_print", "formal_entry_rule", "fresh_l2_book"],
  },
  PRV: {
    code: "PRV",
    name: "Precision Reversal",
    family: "lax_precision",
    hardStateRequired: true,
    allowRealMoneyWhenProven: false,
    description: "Trade against an overshoot only when finer temperature information proves the coarse public print did not cross the relevant settlement threshold.",
    requiredEvidence: ["precise_omo_semantics_verified", "settlement_transform_verified", "fresh_l2_book"],
  },
  LVP: {
    code: "LVP",
    name: "LAX V-Pair",
    family: "sequential_lax",
    hardStateRequired: false,
    allowRealMoneyWhenProven: false,
    description: "Umbrella for trading both independently justified legs of a LAX collapse-reversal sequence.",
    requiredEvidence: ["independent_signal_for_each_leg", "fresh_l2_books", "no_chart_only_entries"],
  },
  HMF: {
    code: "HMF",
    name: "Hidden-Max Flip",
    family: "sequential_lax",
    hardStateRequired: true,
    allowRealMoneyWhenProven: false,
    description: "LAX V-Pair subtype where the reversal leg is triggered by a newly established hidden maximum.",
    requiredEvidence: ["capturable_hidden_max", "proven_settlement_compatibility", "fresh_l2_book"],
  },
};

// Phase 1 intentionally contains only the simplest single-leg hard-state trade.
// Every other strategy remains paper/shadow until its additional execution risk
// (multi-leg atomicity, forecast risk, precision transform, or sequencing) has
// been independently validated.
export const REAL_MONEY_PHASE_1: StrategyCode[] = ["DBN"];
export const SHADOW_ONLY_UNTIL_VALIDATED: StrategyCode[] = ["DSN", "SBK", "HSR", "WTY", "RMO", "PRV", "LVP", "HMF"];

export function strategy(code: StrategyCode) {
  return STRATEGIES[code];
}
