# Step 4I-C verification — end-to-end canonical explainability

Status: **PASS**

Branch: `paper-rigour-v2`

PR: #5

Code-complete head: `d63fd05b73897fac39ab43c41a844b5eea790f27`

GitHub Actions: **Paper Trader CI run 547 (`32762649239`)**

## Real-Postgres chain proved

`paper_collector/test_explainability_postgres.py` builds a complete synthetic canonical benchmark case inside a real Postgres transaction after all production migrations:

```text
exact immutable ASOS raw response
  -> precise T-group evidence + six-hour-max evidence
  -> canonical hard-state transition >= 88F
  -> multiple newly dead buckets
  -> exact traded BucketElimination
  -> causal Kalshi L2 snapshot identity
  -> filled benchmark paper order
  -> immutable exchange market settlement
  -> settlement audit PASS
  -> one deterministic order explanation
```

The explanation is then checked against the database itself:

- exact market/event/station/date;
- exact hard-state id and 88F lower bound;
- both supporting evidence derivations;
- exact raw source and SHA-256;
- distinct observation vs Mercury receipt clocks;
- exact elimination id and three-market dead set;
- L2-only execution identity resolving to the actual `market_data_journal` snapshot row and connection/sequence;
- exchange settlement audit identity and result;
- byte-identical canonical explanation on a second independent read.

## Malformed-source regression

The same real-Postgres test inserts a second immutable ASOS source containing off-lattice `T0310` evidence. The downstream diagnostic sweep must:

- persist `ASOS_OFF_LATTICE_EVIDENCE` as an `integrity_failure`;
- retain the exact raw-source id;
- produce **zero** evidence-source links from that malformed raw record, so it cannot participate in any hard-state authorization.

This proves the explainability layer covers both the successful benchmark chain and the fail-closed chain.

## Full verification

Run **547** completed green:

- standard Python suite: **255 tests, 0 failures**
- Python compile: **PASS**
- collector Docker build: **PASS**
- dependency imports: **PASS**
- Node checks: **PASS**
- fresh Postgres migrations: **PASS**
- SQL013/016/017/018/019/020/021 regressions: **PASS**
- dedicated real-Postgres end-to-end explainability test: **PASS**

## Step 4I completion

With 4I-A, 4I-B and 4I-C green, **Step 4I is PASS**.

Next canonical work: **Step 4J — deterministic replay**.

No merge or deployment occurred.
