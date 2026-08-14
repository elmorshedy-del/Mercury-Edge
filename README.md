# Mercury Edge

Mercury Edge is a Railway-ready research dashboard for testing whether official-station weather information reaches prediction-market prices with a measurable lag. Its core rule is simple: preserve every clock and never use information before it was available.

The current release implements the deterministic receipt-to-repricing engine, persistent website-controlled research jobs, and three source-linked case studies. It deliberately labels missing receipt timestamps and minute-candle execution limits instead of filling them with assumptions.

## What it measures

Every record keeps four distinct times:

1. `observed_at` — when the station measured the weather.
2. `received_at` — when the primary public source exposed the report.
3. `ingested_at` — when Mercury Edge discovered and stored it.
4. `captured_at` — the timestamp of the market quote or candle.

The evidence is classified before it is scored:

- **Market repricing latency:** a settlement-compatible observation makes a lower bracket impossible, but that bracket remains priced.
- **Publication latency:** a high occurred earlier than it appeared in an official product such as a Daily Summary Message (DSM).
- **Trajectory mispricing:** a bracket looks probabilistically wrong given only as-of-cutoff meteorology. This class requires a separately validated forecast model; the deterministic engine does not pretend to solve it.

## Data sources

| Source | Purpose | Important clock |
|---|---|---|
| [NOAA Aviation Weather Center API](https://aviationweather.gov/data/api/) | METAR and SPECI reports | `receiptTime` |
| [NWS API](https://api.weather.gov/) | DSM and Daily Climate Report (CLI) products | product `issuanceTime` |
| [NOAA MADIS OMO](https://madis.ncep.noaa.gov/madis_OMO.shtml) | One-minute ASOS observations | source receipt when available |
| [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/) | High-frequency historical discovery and raw products | discovery time only for pages without receipt metadata |
| [Kalshi public API](https://docs.kalshi.com/) | Events, contract bands, and one-minute candles | candle end time |

Archived IEM high-frequency rows that lack the original source receipt are stored with `receipt_quality=discovery_only`. They can inform station trajectory studies, but the engine automatically rejects them as executable latency evidence.

## Local setup

Requirements: Node.js 22 and, for persistent mode, PostgreSQL 16+.

```bash
npm install
cp .env.example .env.local
npm run dev
```

Without `DATABASE_URL`, the dashboard opens in verified-case mode. With PostgreSQL configured:

```bash
npm run migrate
npm run ingest -- --stations=KNYC,KPHL
npm run worker
npm run jobs:worker
```

Use the website control plane for normal backfills and backtests. The `backfill` and `backtest` command-line scripts remain available for local development or recovery, but production research should be queued from the dashboard so progress, retries, dependencies, and operator actions are retained in PostgreSQL.

## Website-controlled research

The **Research control plane** on the dashboard lets an authorized operator:

- choose a date range and any configured city set;
- queue ingestion followed by a dependent backtest, ingestion alone, or a backtest over already-stored data;
- pause, resume, cancel, or retry without losing completed city-days;
- inspect immutable job events, item attempts, failures, coverage, and latest stored results;
- leave the worker idle until a button is pressed—deploying the service does not start a research run.

The control key is the same `INGEST_TOKEN` stored on the Railway web service. It is submitted as a Bearer token and kept only in the browser tab's `sessionStorage`; it is never stored in the application database. A pipeline's backtest remains blocked until its parent backfill succeeds with no unresolved city-day failures. Retrying a warning backfill reuses completed items and retries only failed work.

Research work is split into idempotent station-day items. Workers claim jobs and items with PostgreSQL row locks, heartbeat while running, and safely recover stale work after a process restart. Pause and cancel requests take effect at the next atomic boundary, so a source response already in flight is allowed to finish and be audited.

Run all quality gates with:

```bash
npm run check
```

## Railway deployment

1. Create a Railway project from this repository.
2. Add a PostgreSQL service; Railway supplies `DATABASE_URL` to linked services.
3. Set `INGEST_TOKEN`, `SOURCE_USER_AGENT`, and `LIVE_INGEST_ENABLED=0` on the web service.
4. Deploy the web service using the included `Dockerfile` and `/api/health` health check.
5. Add a live-ingestion service from the same repository using `railway.worker.json`. Set `LIVE_INGEST_ENABLED=1`, `POLL_INTERVAL_MS=60000`, and `INGEST_STATIONS=KNYC,KPHL` only on that worker.
6. Add a research-job service from the same repository using `railway.jobs.json`. Set `JOB_POLL_INTERVAL_MS=5000`. This service polls the database queue and remains idle until the website creates a job.
7. Expand `INGEST_STATIONS` only after checking provider rate limits and Railway resource use. All 20 mappings remain available without forcing all 20 probes to run each minute.

The Docker image applies idempotent SQL migrations before starting the Next.js server. Protected write routes require `Authorization: Bearer $INGEST_TOKEN`.

The live worker uses a 20-minute candle overlap and fetches only newly discovered NWS product bodies. Database constraints deduplicate the overlap, preserving continuity after a brief provider or worker outage.

## HTTP API

- `GET /api/health` — web and database status.
- `GET /api/dashboard` — dashboard payload and evidence rows.
- `GET /api/backtests` — recent stored runs.
- `GET /api/results` — latest completed backtest results and stored-data coverage.
- `GET /api/jobs` — persistent research queue.
- `GET /api/jobs/:id` — job items and immutable audit events.
- `POST /api/ingest` — current ingestion bundle; token required.
- `POST /api/jobs` — queue a backfill, backtest, or dependent pipeline; token required.
- `POST /api/jobs/:id/action` — pause, resume, cancel, or retry; token required.
- `POST /api/backtests` — compatibility route that queues a stored-data backtest; token required.

Example:

```bash
curl -X POST https://your-app.up.railway.app/api/jobs \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jobType":"pipeline","from":"2026-08-01","to":"2026-08-13","stations":["KNYC","KPHL"]}'
```

## Backtest contract

The mechanical engine:

- sorts observations by source receipt time, not observation time;
- ignores settlement-incompatible measurements;
- takes only the first quote at or after receipt within a configured tolerance;
- never treats an archive discovery timestamp as an original receipt;
- records failed gates instead of silently dropping unattractive cases;
- reports minute candles as quote proxies, never guaranteed fills;
- separates gross outcome, estimated fees, and net outcome.

See [docs/BACKTEST_PROTOCOL.md](docs/BACKTEST_PROTOCOL.md) for the cross-city and changing-climate validation plan.

## Project map

```text
app/                 Next.js dashboard and protected API routes
components/          Interactive charts, filters, case audits
lib/sources/         NOAA/NWS/IEM/Kalshi adapters
lib/backtest/        Leakage-safe mechanical engine and runner
lib/jobs/            Persistent job repository, ingestion unit, and worker logic
scripts/             Migrations, live ingestion, research worker, recovery CLIs
sql/                 PostgreSQL schema
tests/               Timestamp, parsing, and no-lookahead tests
```

## Scope and risk

Mercury Edge is research software, not an automated trading system. Candle history does not prove displayed size, queue position, slippage, or an executable fill. Any strategy promotion should require a preregistered rule, walk-forward validation, realistic fees, and live paper-trading evidence.
