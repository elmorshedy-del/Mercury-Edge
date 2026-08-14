"use client";

import { useMemo, useState } from "react";
import { STATIONS } from "@/lib/config";
import type { CaseStudy, DashboardPayload, EdgeClass } from "@/lib/types";
import { Alert, ArrowUpRight, Check, Database, Mark } from "./Icons";

const FILTERS: Array<{ value: "all" | EdgeClass; label: string; short: string }> = [
  { value: "all", label: "All evidence", short: "All" },
  { value: "market_repricing", label: "Market repricing", short: "Repricing" },
  { value: "publication_latency", label: "Publication latency", short: "Publication" },
  { value: "trajectory_mispricing", label: "Trajectory mispricing", short: "Trajectory" },
];

const CLASS_LABELS: Record<EdgeClass, string> = {
  market_repricing: "Market repricing",
  publication_latency: "Publication latency",
  trajectory_mispricing: "Trajectory mispricing",
};

const CLASS_NUMBERS: Record<EdgeClass, string> = {
  market_repricing: "01",
  publication_latency: "02",
  trajectory_mispricing: "03",
};

const timezoneByStation = new Map(STATIONS.map((item) => [item.station, item.timezone]));

function seconds(value: number | null) {
  if (value === null) return "—";
  if (value < 60) return `${value}s`;
  const minutes = Math.floor(value / 60);
  const remainder = value % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function money(value: number | null) {
  return value === null ? "—" : `${Math.round(value)}¢`;
}

function time(timestamp: string | null, station: string) {
  if (!timestamp) return "Not measured";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: timezoneByStation.get(station) ?? "UTC",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  }).format(new Date(timestamp));
}

function dateLabel(timestamp: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(`${timestamp}T12:00:00Z`));
}

function Metric({ label, value, note, accent = false }: { label: string; value: string; note: string; accent?: boolean }) {
  return (
    <div className={`metric ${accent ? "metricAccent" : ""}`}>
      <div className="metricTop"><span>{label}</span><span className="metricTick" /></div>
      <strong>{value}</strong>
      <p>{note}</p>
    </div>
  );
}

function ClockTimeline({ item }: { item: CaseStudy }) {
  const publication = Math.max(item.observationToAvailabilitySeconds, 0);
  const reaction = Math.max(item.availabilityToRepriceSeconds ?? 0, 0);
  const scale = Math.max(publication + reaction, 300);
  const publicationWidth = Math.max(3, (publication / scale) * 100);
  const reactionWidth = item.availabilityToRepriceSeconds === null
    ? 0 : Math.max(3, (reaction / scale) * 100);
  return (
    <div className="clockTimeline" aria-label={`${item.station} event timeline`}>
      <div className="clockTrack">
        <span className="clockSegment publication" style={{ width: `${Math.min(publicationWidth, 93)}%` }} />
        {item.repricedAt && <span className="clockSegment reaction" style={{ width: `${Math.min(reactionWidth, 93)}%` }} />}
        <i className="clockDot start" />
        <i className="clockDot available" style={{ left: `${Math.min(publicationWidth, 93)}%` }} />
        {item.repricedAt && <i className="clockDot end" style={{ left: `${Math.min(publicationWidth + reactionWidth, 98)}%` }} />}
      </div>
      <div className="clockLabels">
        <span><small>Observed</small>{time(item.observedAt, item.station)}</span>
        <span><small>Public source</small>{time(item.availableAt, item.station)}</span>
        <span className={!item.repricedAt ? "muted" : ""}><small>Market moved</small>{time(item.repricedAt, item.station)}</span>
      </div>
    </div>
  );
}

function LatencyChart({ data }: { data: DashboardPayload["latencyBuckets"] }) {
  const max = Math.max(...data.map((item) => item.count), 1);
  return (
    <div className="barChart" role="img" aria-label="Distribution of market reaction latency">
      <div className="chartGrid"><i /><i /><i /><i /></div>
      {data.map((item) => (
        <div className="barColumn" key={item.label}>
          <span className="barValue">{item.count}</span>
          <div className="barWell"><i style={{ height: `${Math.max(item.count ? 12 : 2, (item.count / max) * 100)}%` }} /></div>
          <span className="barLabel">{item.label}</span>
        </div>
      ))}
    </div>
  );
}

function ReactionPlot({ cases }: { cases: CaseStudy[] }) {
  const points = cases.filter((item) => item.availabilityToRepriceSeconds !== null);
  const maxX = Math.max(...points.map((item) => item.availabilityToRepriceSeconds ?? 0), 300);
  return (
    <div className="reactionPlot" role="img" aria-label="Source availability to market repricing by case">
      <div className="plotYAxis"><span>Publication</span><span>Repricing</span></div>
      <div className="plotBody">
        <i className="plotRule r1" /><i className="plotRule r2" /><i className="plotRule r3" />
        {points.map((item, index) => {
          const x = 6 + ((item.availabilityToRepriceSeconds ?? 0) / maxX) * 86;
          const y = 25 + (index % 3) * 25;
          return (
            <span className={`plotPoint point${index + 1}`} style={{ left: `${x}%`, top: `${y}%` }} key={item.id}>
              <i />
              <b>{item.station}</b>
              <small>{seconds(item.availabilityToRepriceSeconds)}</small>
            </span>
          );
        })}
        {!points.length && <span className="emptyPlot">Run a backtest to populate</span>}
      </div>
      <div className="plotXAxis"><span>0</span><span>source → market reaction</span><span>{seconds(maxX)}</span></div>
    </div>
  );
}

function CaseCard({ item, index }: { item: CaseStudy; index: number }) {
  return (
    <article className="caseCard">
      <div className="caseIndex">{String(index + 1).padStart(2, "0")}</div>
      <div className="caseMain">
        <div className="caseMeta">
          <span className={`quality ${item.evidenceQuality}`}>{item.evidenceQuality === "high" ? <Check /> : <Alert />}{item.evidenceQuality} evidence</span>
          <span>{dateLabel(item.date)}</span>
          <span>{item.city} · {item.station}</span>
        </div>
        <div className="caseHeading">
          <div>
            <span className="caseClass">{CLASS_NUMBERS[item.edgeClass]} / {CLASS_LABELS[item.edgeClass]}</span>
            <h3>{item.headline}</h3>
          </div>
          <div className="caseOutcome">
            <small>{item.grossProfitCents === null ? "Measured lag" : "Illustrative gross"}</small>
            <strong>{item.grossProfitCents === null ? seconds(item.totalLatencySeconds) : money(item.grossProfitCents)}</strong>
          </div>
        </div>
        <ClockTimeline item={item} />
        <div className="caseFacts">
          <div><span>Contract</span><strong>{item.contract}</strong></div>
          <div><span>Entry proxy</span><strong>{money(item.entryCents)}</strong></div>
          <div><span>Final high</span><strong>{item.finalHighF === null ? "—" : `${item.finalHighF}°F`}</strong></div>
          <div><span>Executable gate</span><strong className={item.executableProxy ? "positive" : "caution"}>{item.executableProxy ? "Passed*" : "Failed"}</strong></div>
        </div>
        <p className="caseNote">{item.note}</p>
        <div className="caseFooter">
          <span><Alert /> {item.limitation}</span>
          <div>{item.sources.map((source) => <a href={source.url} target="_blank" rel="noreferrer" key={source.url}>{source.label}<ArrowUpRight /></a>)}</div>
        </div>
      </div>
    </article>
  );
}

export function MercuryDashboard({ initialData }: { initialData: DashboardPayload }) {
  const [filter, setFilter] = useState<"all" | EdgeClass>("all");
  const [citySearch, setCitySearch] = useState("");
  const cases = useMemo(
    () => initialData.cases.filter((item) => filter === "all" || item.edgeClass === filter),
    [filter, initialData.cases],
  );
  const cities = initialData.cities.filter((item) =>
    `${item.city} ${item.station}`.toLowerCase().includes(citySearch.toLowerCase()),
  );

  return (
    <main>
      <header className="nav shell">
        <a className="brand" href="#top" aria-label="Mercury Edge home"><Mark /><span>Mercury <b>Edge</b></span></a>
        <nav>
          <a href="#evidence">Evidence</a>
          <a href="#coverage">Coverage</a>
          <a href="#method">Method</a>
        </nav>
        <div className="navStatus"><i />{initialData.mode === "database" ? "Live database" : "Verified case mode"}</div>
      </header>

      <section className="hero shell" id="top">
        <div className="heroEyebrow"><span>Weather market intelligence</span><i /><span>v0.1 research build</span></div>
        <div className="heroGrid">
          <div className="heroCopy">
            <h1>Weather has<br /><em>four clocks.</em><br />Markets price one.</h1>
            <p>Mercury Edge measures the gap between when a temperature occurs, when a source receives it, when we ingest it, and when the market finally reacts.</p>
            <div className="heroActions">
              <a href="#evidence" className="primaryButton">Explore the evidence <ArrowUpRight /></a>
              <a href="#method" className="textButton">Read the leakage rules</a>
            </div>
          </div>
          <div className="heroInstrument">
            <div className="instrumentHeader"><span>Clock separation</span><span className="liveTag"><i />Audited</span></div>
            <div className="radarWrap">
              <svg viewBox="0 0 360 270" aria-hidden="true">
                <defs>
                  <radialGradient id="radarGlow"><stop offset="0" stopColor="#b8f0bd" stopOpacity=".22" /><stop offset="1" stopColor="#b8f0bd" stopOpacity="0" /></radialGradient>
                </defs>
                <circle cx="180" cy="135" r="116" fill="url(#radarGlow)" />
                <circle cx="180" cy="135" r="100" className="radarCircle" />
                <circle cx="180" cy="135" r="72" className="radarCircle" />
                <circle cx="180" cy="135" r="44" className="radarCircle" />
                <path d="M180 25v220M70 135h220M102 57l156 156M258 57 102 213" className="radarLine" />
                <path d="M180 135 116 76A92 92 0 0 1 252 79Z" className="radarSweep" />
                <circle cx="116" cy="76" r="5" className="radarBlip" />
                <circle cx="247" cy="90" r="4" className="radarBlip secondary" />
                <circle cx="214" cy="197" r="3.5" className="radarBlip tertiary" />
              </svg>
              <div className="radarCenter"><small>Median<br />reaction</small><strong>{seconds(initialData.headline.medianMarketReactionSeconds)}</strong></div>
              <span className="radarLabel l1">Observed</span><span className="radarLabel l2">Published</span><span className="radarLabel l3">Ingested</span><span className="radarLabel l4">Repriced</span>
            </div>
            <div className="instrumentFooter"><span>Generated</span><time>{new Date(initialData.generatedAt).toLocaleString()}</time></div>
          </div>
        </div>
      </section>

      <section className="metricsBand">
        <div className="shell metricsGrid">
          <Metric label="High-confidence cases" value={String(initialData.headline.verifiedCases)} note={`${initialData.headline.candidateCases} candidate case kept separate`} accent />
          <Metric label="Cities configured" value={String(initialData.headline.citiesCovered)} note="Each station is modeled independently" />
          <Metric label="Median source → market" value={seconds(initialData.headline.medianMarketReactionSeconds)} note="Only cases with both timestamps" />
          <Metric label="Illustrative gross" value={money(initialData.headline.grossProfitCents)} note="Observed examples; not a portfolio return" />
        </div>
      </section>

      <section className="section shell" id="evidence">
        <div className="sectionIntro">
          <div><span className="kicker">Evidence lab</span><h2>One phenomenon.<br />Three different edges.</h2></div>
          <p>A price move after a weather report is not automatically tradable alpha. The engine first classifies the clock that moved, then applies receipt-time and execution gates.</p>
        </div>
        <div className="filterBar" role="tablist" aria-label="Evidence classes">
          {FILTERS.map((item) => (
            <button key={item.value} className={filter === item.value ? "active" : ""} onClick={() => setFilter(item.value)} role="tab" aria-selected={filter === item.value}>
              <span>{item.short}</span><small>{item.label}</small>
            </button>
          ))}
          <div className="filterCount">{cases.length} {cases.length === 1 ? "case" : "cases"}</div>
        </div>

        <div className="analysisGrid">
          <div className="panel latencyPanel">
            <div className="panelHeader"><div><span>Distribution</span><h3>Market reaction latency</h3></div><small>source receipt → quote response</small></div>
            <LatencyChart data={initialData.latencyBuckets} />
          </div>
          <div className="panel plotPanel">
            <div className="panelHeader"><div><span>Case map</span><h3>Clock-to-clock distance</h3></div><small>measured, never inferred</small></div>
            <ReactionPlot cases={cases} />
          </div>
        </div>

        <div className="caseList">
          {cases.map((item, index) => <CaseCard item={item} index={index} key={item.id} />)}
          {!cases.length && <div className="emptyState">No case in this evidence class yet.</div>}
        </div>
      </section>

      <section className="coverageSection" id="coverage">
        <div className="shell">
          <div className="coverageHeader">
            <div><span className="kicker">Cross-city research</span><h2>Twenty stations.<br />No station mixing.</h2></div>
            <div className="searchBox"><span>⌕</span><input value={citySearch} onChange={(event) => setCitySearch(event.target.value)} placeholder="Filter city or station" aria-label="Filter cities" /></div>
          </div>
          <div className="coverageTable" role="table" aria-label="City coverage">
            <div className="tableRow tableHead" role="row"><span>City / official station</span><span>Status</span><span>Cases</span><span>Median lag</span><span>Win rate*</span><span>Avg gross*</span></div>
            {cities.map((city) => (
              <div className="tableRow" role="row" key={city.station}>
                <span><b>{city.city}</b><small>{city.station}</small></span>
                <span><i className={`coverageDot ${city.coverage}`} />{city.coverage}</span>
                <span>{city.cases}</span>
                <span>{seconds(city.medianLagSeconds)}</span>
                <span>{city.winRate === null ? "—" : `${Math.round(city.winRate * 100)}%`}</span>
                <span>{money(city.avgGrossProfitCents)}</span>
              </div>
            ))}
          </div>
          <p className="tableFootnote">* Quote-proxy statistics only. Fees, slippage, queue position, and available size must be modeled before any execution claim.</p>
        </div>
      </section>

      <section className="section shell" id="method">
        <div className="sectionIntro methodIntro">
          <div><span className="kicker">Research contract</span><h2>Fail closed.<br />Learn honestly.</h2></div>
          <p>The backtester is designed to reject attractive-looking evidence when its timestamps cannot support the claim. Missing receipt time is a classification change, not a blank to fill.</p>
        </div>
        <div className="methodGrid">
          <div className="methodFlow">
            {[
              ["01", "Occurrence", "What the station measured", "observed_at"],
              ["02", "Availability", "When the primary source exposed it", "received_at"],
              ["03", "Discovery", "When Mercury Edge first saw it", "ingested_at"],
              ["04", "Reaction", "First market quote that reflects it", "captured_at"],
            ].map(([number, title, copy, field], index) => (
              <div className="methodStep" key={number}><span>{number}</span><div><h3>{title}</h3><p>{copy}</p><code>{field}</code></div>{index < 3 && <i />}</div>
            ))}
          </div>
          <aside className="rulesCard">
            <div className="rulesHeader"><Database /><span>Leakage firewall</span></div>
            <h3>A signal may use only information public by its cutoff.</h3>
            <ul>
              <li><Check /> Original source receipt required for latency P&amp;L</li>
              <li><Check /> First quote at or after receipt; never before</li>
              <li><Check /> Official station kept distinct from nearby airports</li>
              <li><Check /> Deterministic invalidation kept separate from trajectory odds</li>
              <li><Check /> Backfilled reports retain discovery time and failed gate</li>
            </ul>
            <div className="ruleVersion"><span>Active engine</span><strong>latency-v0.1.0</strong></div>
          </aside>
        </div>

        <div className="sourcesPanel">
          <div className="sourcesTitle"><span>Source probes</span><small>Independent health; partial failure is visible</small></div>
          <div className="sourcesGrid">
            {initialData.sourceHealth.map((source) => (
              <div className="sourceItem" key={source.name}>
                <i className={source.status} />
                <div><strong>{source.name}</strong><span>{source.purpose}</span></div>
                <small>{source.lastSeen ? time(source.lastSeen, "KNYC") : source.status}</small>
              </div>
            ))}
          </div>
        </div>
      </section>

      <footer>
        <div className="shell footerGrid">
          <div><a className="brand" href="#top"><Mark /><span>Mercury <b>Edge</b></span></a><p>Timestamp-faithful weather-market research.<br />Evidence before narrative.</p></div>
          <div><span>Data</span><a href="https://aviationweather.gov/data/api/">NOAA AWC</a><a href="https://api.weather.gov/">NWS API</a><a href="https://madis.ncep.noaa.gov/madis_OMO.shtml">NOAA MADIS</a></div>
          <div><span>Build</span><a href="/api/health">System health</a><a href="/api/dashboard">Dashboard JSON</a><a href="#method">Methodology</a></div>
          <div className="footerNote"><Alert /><p>Research software only. Prediction markets involve financial risk; historical quote proxies do not guarantee executable fills.</p></div>
        </div>
      </footer>
    </main>
  );
}
