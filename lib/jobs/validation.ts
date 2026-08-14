import { STATIONS } from "../config";
import type { ResearchJobParameters } from "./types";

const DATE = /^\d{4}-\d{2}-\d{2}$/;

export function validateJobParameters(input: unknown): ResearchJobParameters {
  if (!input || typeof input !== "object") throw new Error("A job configuration is required");
  const value = input as Record<string, unknown>;
  if (typeof value.from !== "string" || !DATE.test(value.from)) throw new Error("from must be YYYY-MM-DD");
  if (typeof value.to !== "string" || !DATE.test(value.to)) throw new Error("to must be YYYY-MM-DD");
  const from = new Date(`${value.from}T12:00:00Z`);
  const to = new Date(`${value.to}T12:00:00Z`);
  if (!Number.isFinite(from.getTime()) || !Number.isFinite(to.getTime()) || to < from) {
    throw new Error("to must be on or after from");
  }
  const days = Math.floor((to.getTime() - from.getTime()) / 86_400_000) + 1;
  if (days > 730) throw new Error("One job may cover at most 730 days");
  if (!Array.isArray(value.stations)) throw new Error("stations must be an array");
  const configured = new Set(STATIONS.map((station) => station.station));
  const stations = [...new Set(value.stations.map(String).map((item) => item.trim().toUpperCase()))]
    .filter((station) => configured.has(station));
  if (!stations.length) throw new Error("Select at least one configured station");
  if (stations.length !== value.stations.length) throw new Error("One or more stations are invalid or duplicated");
  return { from: value.from, to: value.to, stations };
}
