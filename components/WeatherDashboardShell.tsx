"use client";

import { useMemo, useState } from "react";
import { WeatherDashboardClient } from "@/components/WeatherDashboardClient";
import { WeatherReactionDesk } from "@/components/WeatherReactionDesk";
import { SixHourReleaseStrip } from "@/components/SixHourReleaseStrip";
import { DsmReleaseStrip } from "@/components/DsmReleaseStrip";
import { FullDayHfArchive } from "@/components/FullDayHfArchive";
import { LaxCapWatch } from "@/components/LaxCapWatch";
import { WeatherHistoryPanel } from "@/components/WeatherHistoryPanel";
import { WeatherStudyTable } from "@/components/WeatherStudyTable";
import styles from "@/app/weather-dashboard/weather-shell.module.css";

const CITIES = [
  { stid: "KNYC", city: "NYC", long: "Central Park", timezone: "America/New_York" },
  { stid: "KPHL", city: "Philadelphia", long: "Philadelphia", timezone: "America/New_York" },
  { stid: "KLAX", city: "LAX", long: "Los Angeles", timezone: "America/Los_Angeles" },
  { stid: "KDEN", city: "Denver", long: "Denver", timezone: "America/Denver" },
  { stid: "KSEA", city: "Seattle", long: "Seattle", timezone: "America/Los_Angeles" },
] as const;

type View = "live" | "market" | "reports" | "history" | "study";

const VIEWS: Array<{ key: View; label: string; detail: string }> = [
  { key: "live", label: "Live", detail: "ASOS + TWC" },
  { key: "market", label: "Market", detail: "Weather ↔ Kalshi" },
  { key: "reports", label: "Reports", detail: "6h / DSM / HF" },
  { key: "history", label: "Archive", detail: "Frozen day" },
  { key: "study", label: "Study", detail: "TWC accuracy" },
];

export function WeatherDashboardShell() {
  const [selectedStid, setSelectedStid] = useState("KNYC");
  const [view, setView] = useState<View>("live");
  const station = useMemo(() => CITIES.find((item) => item.stid === selectedStid) ?? CITIES[0], [selectedStid]);

  return (
    <div className={styles.shell}>
      <header className={styles.commandBar}>
        <div className={styles.brandRow}>
          <div>
            <span>Mercury Edge</span>
            <h1>Weather Lab</h1>
          </div>
          <div className={styles.context}>
            <b>{station.city}</b>
            <small>{station.stid} · one city controls the whole page</small>
          </div>
        </div>

        <nav className={styles.cityNav} aria-label="Weather city">
          {CITIES.map((item) => (
            <button
              key={item.stid}
              className={selectedStid === item.stid ? styles.activeCity : ""}
              onClick={() => setSelectedStid(item.stid)}
            >
              <b>{item.city}</b>
              <small>{item.stid}</small>
            </button>
          ))}
        </nav>

        <nav className={styles.viewNav} aria-label="Weather dashboard section">
          {VIEWS.map((item) => (
            <button key={item.key} className={view === item.key ? styles.activeView : ""} onClick={() => setView(item.key)}>
              <b>{item.label}</b>
              <small>{item.detail}</small>
            </button>
          ))}
        </nav>
      </header>

      <main className={styles.content}>
        {view === "live" && (
          <div className={styles.stack}>
            <WeatherDashboardClient selectedStid={selectedStid} embedded />
            {selectedStid === "KLAX" && <LaxCapWatch selectedStid={selectedStid} />}
          </div>
        )}

        {view === "market" && (
          <WeatherReactionDesk selectedStid={selectedStid} embedded />
        )}

        {view === "reports" && (
          <div className={styles.stack}>
            <div className={styles.sectionLead}>
              <span>Official release tape</span>
              <h2>{station.long}: maxima, DSM and high-frequency observations</h2>
              <p>Same selected city throughout. No second station picker lower on the page.</p>
            </div>
            <SixHourReleaseStrip selectedStid={selectedStid} embedded />
            <DsmReleaseStrip selectedStid={selectedStid} embedded />
            <FullDayHfArchive selectedStid={selectedStid} embedded />
          </div>
        )}

        {view === "history" && (
          <WeatherHistoryPanel stid={station.stid} city={station.long} timezone={station.timezone} />
        )}

        {view === "study" && <WeatherStudyTable />}
      </main>
    </div>
  );
}
