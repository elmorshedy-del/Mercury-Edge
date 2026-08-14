import { NWS_BASE_URL } from "../config";
import { fetchJson } from "../http";

type ProductIndex = {
  "@graph": Array<{
    id: string;
    issuanceTime: string;
    productCode: string;
    productName: string;
  }>;
};

type ProductDetail = {
  id: string;
  issuanceTime: string;
  productCode: string;
  productName: string;
  productText: string;
  issuingOffice: string;
  [key: string]: unknown;
};

export type ParsedProduct = {
  productId: string;
  productType: "DSM" | "CLI";
  issuedAt: string;
  reportedHighF: number | null;
  highObservedAt: string | null;
  rawText: string;
  sourceUrl: string;
  payload: ProductDetail;
};

const standardOffsets: Record<string, number> = {
  "America/New_York": -5,
  "America/Chicago": -6,
  "America/Denver": -7,
  "America/Los_Angeles": -8,
  "America/Phoenix": -7,
};

function parseSignedTemperature(token: string) {
  if (/^M\d+$/.test(token)) return -Number(token.slice(1));
  const value = Number(token);
  return Number.isFinite(value) ? value : null;
}

export function parseDsm(text: string, issuedAt: string, timezone: string) {
  const match = text.match(/\bK[A-Z0-9]{3}\s+DS\s+\d{4}\s+(\d{2})\/(\d{2})\s+(M?\d{2,3})(\d{4})\//);
  if (!match) return { high: null, highAt: null };
  const high = parseSignedTemperature(match[3]);
  const hour = Number(match[4].slice(0, 2));
  const minute = Number(match[4].slice(2));
  const issued = new Date(issuedAt);
  const offset = standardOffsets[timezone];
  if (offset === undefined) return { high, highAt: null };
  const highAt = new Date(
    Date.UTC(issued.getUTCFullYear(), Number(match[2]) - 1, Number(match[1]), hour - offset, minute),
  );
  return { high, highAt: highAt.toISOString() };
}

export function parseCli(text: string) {
  const maximum = text.match(/^\s*MAXIMUM\s+(-?\d{1,3})\b/im);
  return maximum ? Number(maximum[1]) : null;
}

export async function getProducts(
  productType: "DSM" | "CLI",
  location: string,
  timezone: string,
  limit = 8,
): Promise<ParsedProduct[]> {
  const indexUrl = `${NWS_BASE_URL}/products/types/${productType}/locations/${location}`;
  const index = await fetchJson<ProductIndex>(indexUrl);
  const items = index["@graph"].slice(0, limit);
  const results: ParsedProduct[] = [];

  for (const item of items) {
    const sourceUrl = `${NWS_BASE_URL}/products/${item.id}`;
    const detail = await fetchJson<ProductDetail>(sourceUrl);
    const dsm = productType === "DSM"
      ? parseDsm(detail.productText, detail.issuanceTime, timezone)
      : { high: parseCli(detail.productText), highAt: null };
    results.push({
      productId: detail.id,
      productType,
      issuedAt: new Date(detail.issuanceTime).toISOString(),
      reportedHighF: dsm.high,
      highObservedAt: dsm.highAt,
      rawText: detail.productText,
      sourceUrl,
      payload: detail,
    });
  }
  return results;
}
