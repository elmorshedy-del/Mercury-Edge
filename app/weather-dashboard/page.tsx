import type { Metadata } from "next";
import { WeatherDashboardClient } from "@/components/WeatherDashboardClient";
import { SixHourReleaseStrip } from "@/components/SixHourReleaseStrip";

export const metadata: Metadata = {
  title: "Weather Reports | Mercury Edge",
  description: "Live ASOS high-frequency, hourly, SPECI, and six-hour temperature reports.",
};

export const dynamic = "force-dynamic";

export default function WeatherDashboardPage() {
  return (
    <>
      <SixHourReleaseStrip />
      <WeatherDashboardClient />
    </>
  );
}
