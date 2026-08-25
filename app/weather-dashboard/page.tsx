import type { Metadata } from "next";
import { WeatherDashboardClient } from "@/components/WeatherDashboardClient";
import { SixHourReleaseStrip } from "@/components/SixHourReleaseStrip";
import { DsmReleaseStrip } from "@/components/DsmReleaseStrip";

export const metadata: Metadata = {
  title: "Weather Reports | Mercury Edge",
  description: "Live ASOS high-frequency, hourly, SPECI, six-hour, and DSM temperature reports.",
};

export const dynamic = "force-dynamic";

export default function WeatherDashboardPage() {
  return (
    <>
      <SixHourReleaseStrip />
      <DsmReleaseStrip />
      <WeatherDashboardClient />
    </>
  );
}
