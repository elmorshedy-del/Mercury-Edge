export type MetarMaximumKind = "metar_6h_max" | "metar_24h_max";

export type ParsedMetarHardState = {
  preciseTemperatureF: number | null;
  maximumTemperatureF: number | null;
  maximumKind: MetarMaximumKind | null;
};

const cToF = (celsius: number) => (celsius * 9) / 5 + 32;

function signedTenths(sign: string, digits: string) {
  const value = Number(digits) / 10;
  return sign === "1" ? -value : value;
}

/**
 * Parse only the unambiguous temperature groups defined for U.S. METAR
 * remarks. Keeping this separate from the decoded API fields lets the hard
 * state audit show exactly which raw token crossed a contract boundary.
 */
export function parseMetarHardState(rawText?: string | null): ParsedMetarHardState {
  if (!rawText) {
    return { preciseTemperatureF: null, maximumTemperatureF: null, maximumKind: null };
  }

  const tokens = rawText.trim().replace(/=$/, "").split(/\s+/);
  let preciseTemperatureF: number | null = null;
  let sixHourMaximumF: number | null = null;
  let dayMaximumF: number | null = null;

  for (const token of tokens) {
    const precise = /^T([01])(\d{3})([01])(\d{3})$/.exec(token);
    if (precise) {
      preciseTemperatureF = cToF(signedTenths(precise[1], precise[2]));
      continue;
    }

    const sixHour = /^1([01])(\d{3})$/.exec(token);
    if (sixHour) {
      sixHourMaximumF = cToF(signedTenths(sixHour[1], sixHour[2]));
      continue;
    }

    const fullDay = /^4([01])(\d{3})([01])(\d{3})$/.exec(token);
    if (fullDay) dayMaximumF = cToF(signedTenths(fullDay[1], fullDay[2]));
  }

  if (dayMaximumF !== null && (sixHourMaximumF === null || dayMaximumF >= sixHourMaximumF)) {
    return {
      preciseTemperatureF,
      maximumTemperatureF: dayMaximumF,
      maximumKind: "metar_24h_max",
    };
  }
  return {
    preciseTemperatureF,
    maximumTemperatureF: sixHourMaximumF,
    maximumKind: sixHourMaximumF === null ? null : "metar_6h_max",
  };
}
