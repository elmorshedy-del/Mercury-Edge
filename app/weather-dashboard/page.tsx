import type { Metadata } from "next";
import { WeatherDashboardClient } from "@/components/WeatherDashboardClient";
import { SixHourReleaseStrip } from "@/components/SixHourReleaseStrip";
import { DsmReleaseStrip } from "@/components/DsmReleaseStrip";
import { WeatherReactionDesk } from "@/components/WeatherReactionDesk";
import { LaxCapWatch } from "@/components/LaxCapWatch";
import { FullDayHfArchive } from "@/components/FullDayHfArchive";

export const metadata: Metadata = {
  title: "Weather Reports | Mercury Edge",
  description: "Live ASOS reports with NWS forecast-anchor divergence, LAX cap signals and synchronized Kalshi market reaction.",
};

export const dynamic = "force-dynamic";

export default function WeatherDashboardPage() {
  return (
    <>
      <SixHourReleaseStrip />
      <DsmReleaseStrip />
      <WeatherReactionDesk />
      <LaxCapWatch />
      <FullDayHfArchive />
      <WeatherDashboardClient />
    </>
  );
}
