import type { Metadata } from "next";
import { WeatherDashboardClient } from "@/components/WeatherDashboardClient";
import { SixHourReleaseStrip } from "@/components/SixHourReleaseStrip";
import { DsmReleaseStrip } from "@/components/DsmReleaseStrip";
import { WeatherReactionDesk } from "@/components/WeatherReactionDesk";

export const metadata: Metadata = {
  title: "Weather Reports | Mercury Edge",
  description: "Live ASOS reports with TWC forecast-anchor divergence and synchronized Kalshi market reaction.",
};

export const dynamic = "force-dynamic";

export default function WeatherDashboardPage() {
  return (
    <>
      <SixHourReleaseStrip />
      <DsmReleaseStrip />
      <WeatherReactionDesk />
      <WeatherDashboardClient />
    </>
  );
}
