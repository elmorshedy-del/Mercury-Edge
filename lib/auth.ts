import { timingSafeEqual } from "node:crypto";
import type { NextRequest } from "next/server";

export function authorized(request: NextRequest) {
  const expected = process.env.INGEST_TOKEN;
  const supplied = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!expected || !supplied) return false;
  const left = Buffer.from(expected);
  const right = Buffer.from(supplied);
  return left.length === right.length && timingSafeEqual(left, right);
}
