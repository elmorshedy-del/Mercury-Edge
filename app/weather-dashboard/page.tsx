import type { Metadata } from "next";
import { WeatherDashboardShell } from "@/components/WeatherDashboardShell";

export const metadata: Metadata = {
  title: "Weather Lab | Mercury Edge",
  description: "Unified ASOS, TWC, Kalshi reaction, archived-day and forecast-accuracy workspace.",
};

export const dynamic = "force-dynamic";

export default function WeatherDashboardPage() {
  return <WeatherDashboardShell />;
}
