# Step 4J-A verification — causal replay manifest and event stream

Status: **PASS**

GitHub Actions: **run 561 (`32764624225`)**

Verified branch head: `03449bbf1cc898efa3b6f602e1713ac44e521240`

Primary commits:
- `ecf3ddfbd2fe2ae4f11360e59218248082319898` — locked Step 4J plan / initial replay-domain implementation.
- `fb959afd79516aa80bda0a2b8141abe4f988c963` — Step 4J-A regression suite.
- `eadd3ccec1879525ba6f2ee823a757f16304b2a8` — collector image includes replay domain.
- `03449bbf1cc898efa3b6f602e1713ac44e521240` — CI includes replay-domain tests.

## What passed

- deterministic source-neutral `ReplayEvent` ordering independent of SQL/input order;
- Mercury receipt/knowledge time controls replay availability, not physical observation time;
- live MADIS and later archive imports are structurally distinct;
- archive imports are benchmark-inadmissible and remain causally late even if their physical observation time is old;
- rule snapshots are available only at `captured_at`;
- market messages use Mercury receipt with stable sequence/id tie-breaks;
- identical immutable source inputs + filters + version bundle produce identical source-input and manifest hashes;
- changing only a component version changes manifest identity while preserving source-input hash.

## Full regression result

- **264 Python tests, 0 failures**;
- Python compile PASS;
- collector Docker build PASS;
- Node check PASS;
- full Postgres migrations PASS;
- SQL013/016/017/018/019/020/021 regressions PASS;
- real-Postgres Step 4I explainability regression PASS.

Step 4J-A is therefore complete. Next: 4J-B canonical hard-state / elimination reconstruction.
