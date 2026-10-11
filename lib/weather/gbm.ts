// Minimal evaluator for LightGBM multiclass tree ensembles exported by
// research/manh_knyc/train.py. Only numerical "<=" splits are supported, which is all the
// exporter emits. Missing-value routing mirrors LightGBM's NumericalDecision.

export type ExportedTree = {
  f: number[]; // split feature index per internal node
  t: number[]; // threshold per internal node
  d: number[]; // 1 = missing values go left
  m: number[]; // missing type: 0 None (NaN -> 0), 1 Zero, 2 NaN
  l: number[]; // left child: >= 0 internal node, < 0 leaf (-index - 1)
  r: number[]; // right child, same encoding
  v: number[]; // leaf values
};

export type ExportedBooster = {
  features: string[];
  num_class: number;
  class_offset: number;
  temperature?: number; // softmax temperature fitted on a held-out year (1 = none)
  trees: ExportedTree[];
};

const ZERO_THRESHOLD = 1e-35;

function treeValue(tree: ExportedTree, x: ReadonlyArray<number>): number {
  if (tree.f.length === 0) return tree.v[0];
  let node = 0;
  for (;;) {
    let value = x[tree.f[node]];
    const missingType = tree.m[node];
    if (Number.isNaN(value) && missingType !== 2) value = 0;
    let goLeft: boolean;
    if ((missingType === 1 && Math.abs(value) <= ZERO_THRESHOLD) || (missingType === 2 && Number.isNaN(value))) {
      goLeft = tree.d[node] === 1;
    } else {
      goLeft = value <= tree.t[node];
    }
    const next = goLeft ? tree.l[node] : tree.r[node];
    if (next < 0) return tree.v[-next - 1];
    node = next;
  }
}

/** Feature vector in model order; null/undefined/non-finite values become NaN (missing). */
export function featureVector(booster: ExportedBooster, features: Record<string, number | null | undefined>) {
  return booster.features.map((name) => {
    const value = features[name];
    return typeof value === "number" && Number.isFinite(value) ? value : Number.NaN;
  });
}

/** Softmax class probabilities: LightGBM's multiclass predict() with the exported temperature. */
export function predictProba(booster: ExportedBooster, x: ReadonlyArray<number>): number[] {
  const k = booster.num_class;
  const temperature = booster.temperature ?? 1;
  const raw = new Array<number>(k).fill(0);
  booster.trees.forEach((tree, index) => {
    raw[index % k] += treeValue(tree, x) / temperature;
  });
  const max = Math.max(...raw);
  const exp = raw.map((value) => Math.exp(value - max));
  const sum = exp.reduce((acc, value) => acc + value, 0);
  return exp.map((value) => value / sum);
}
