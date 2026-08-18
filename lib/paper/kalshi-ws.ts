import { EventEmitter } from "node:events";
import { createHash, createPrivateKey, createPublicKey, constants, randomBytes, sign } from "node:crypto";
import https from "node:https";
import type { Duplex } from "node:stream";
import { complementPriceFp, formatPriceFp, parsePriceFp } from "./fixed";

const WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
export const KALSHI_WS_PATH = "/trade-api/ws/v2";
export const KALSHI_WS_HOST = "external-api-ws.kalshi.com";

export type KalshiWsEnvelope = {
  id?: number;
  type?: string;
  sid?: number;
  seq?: number;
  msg?: Record<string, unknown>;
  [key: string]: unknown;
};

export function loadKalshiPrivateKey(env: NodeJS.ProcessEnv = process.env) {
  const b64 = env.KALSHI_PRIVATE_KEY_PEM_B64?.trim();
  if (b64) return Buffer.from(b64, "base64").toString("utf8");
  const inline = env.KALSHI_PRIVATE_KEY_PEM?.trim();
  if (inline) return inline.replace(/\\n/g, "\n");
  throw new Error("KALSHI_PRIVATE_KEY_PEM_B64 or KALSHI_PRIVATE_KEY_PEM is required");
}

export function kalshiKeyFingerprint(privateKeyPem: string) {
  const publicDer = createPublicKey(createPrivateKey(privateKeyPem)).export({ type: "spki", format: "der" });
  return createHash("sha256").update(publicDer).digest("hex").slice(0, 24);
}

export function kalshiWsAuthHeaders(input: {
  keyId: string;
  privateKeyPem: string;
  timestampMs?: number;
}) {
  const timestamp = String(input.timestampMs ?? Date.now());
  const message = `${timestamp}GET${KALSHI_WS_PATH}`;
  const signature = sign("sha256", Buffer.from(message), {
    key: input.privateKeyPem,
    padding: constants.RSA_PKCS1_PSS_PADDING,
    saltLength: constants.RSA_PSS_SALTLEN_DIGEST,
  }).toString("base64");
  return {
    "KALSHI-ACCESS-KEY": input.keyId,
    "KALSHI-ACCESS-SIGNATURE": signature,
    "KALSHI-ACCESS-TIMESTAMP": timestamp,
  };
}

type RawWsEvents = {
  open: [];
  message: [text: string];
  close: [code: number, reason: string];
  error: [error: Error];
};

/**
 * Minimal authenticated WebSocket client using Node's HTTPS upgrade path.
 * We intentionally do not negotiate permessage-deflate so replay receives the
 * exact UTF-8 JSON payloads sent by Kalshi without a compression dependency.
 */
export class RawKalshiWebSocket extends EventEmitter<RawWsEvents> {
  private socket: Duplex | null = null;
  private buffer = Buffer.alloc(0);
  private fragmentedOpcode: number | null = null;
  private fragments: Buffer[] = [];
  private closing = false;

  constructor(private readonly config: {
    keyId: string;
    privateKeyPem: string;
    host?: string;
    path?: string;
  }) {
    super();
  }

  connect() {
    if (this.socket) throw new Error("WEBSOCKET_ALREADY_CONNECTED");
    const host = this.config.host ?? KALSHI_WS_HOST;
    const path = this.config.path ?? KALSHI_WS_PATH;
    const wsKey = randomBytes(16).toString("base64");
    const expectedAccept = createHash("sha1").update(wsKey + WS_GUID).digest("base64");
    const auth = kalshiWsAuthHeaders({
      keyId: this.config.keyId,
      privateKeyPem: this.config.privateKeyPem,
    });

    return new Promise<void>((resolve, reject) => {
      const request = https.request({
        hostname: host,
        port: 443,
        path,
        method: "GET",
        headers: {
          ...auth,
          Connection: "Upgrade",
          Upgrade: "websocket",
          "Sec-WebSocket-Key": wsKey,
          "Sec-WebSocket-Version": "13",
        },
      });

      const fail = (error: Error) => {
        request.destroy();
        reject(error);
      };
      request.once("error", fail);
      request.once("response", (response) => {
        fail(new Error(`WEBSOCKET_UPGRADE_REJECTED status=${response.statusCode}`));
      });
      request.once("upgrade", (response, socket, head) => {
        request.removeListener("error", fail);
        const accept = response.headers["sec-websocket-accept"];
        if (response.statusCode !== 101 || accept !== expectedAccept) {
          socket.destroy();
          reject(new Error(`WEBSOCKET_BAD_HANDSHAKE status=${response.statusCode} accept=${String(accept)}`));
          return;
        }
        this.socket = socket;
        this.closing = false;
        socket.on("data", (chunk) => this.feed(Buffer.from(chunk)));
        socket.on("error", (error) => this.emit("error", error));
        socket.on("end", () => {
          this.socket = null;
          this.emit("close", 1006, "socket ended");
        });
        socket.on("close", () => { this.socket = null; });
        if (head.length) this.feed(head);
        this.emit("open");
        resolve();
      });
      request.end();
    });
  }

  sendJson(value: unknown) {
    this.sendFrame(0x1, Buffer.from(JSON.stringify(value), "utf8"));
  }

  close(code = 1000, reason = "") {
    if (!this.socket || this.closing) return;
    this.closing = true;
    const reasonBytes = Buffer.from(reason, "utf8").subarray(0, 123);
    const payload = Buffer.alloc(2 + reasonBytes.length);
    payload.writeUInt16BE(code, 0);
    reasonBytes.copy(payload, 2);
    this.sendFrame(0x8, payload);
    setTimeout(() => this.socket?.destroy(), 1_000).unref();
  }

  private sendFrame(opcode: number, payload: Buffer) {
    if (!this.socket) throw new Error("WEBSOCKET_NOT_CONNECTED");
    const mask = randomBytes(4);
    const header: number[] = [0x80 | opcode];
    if (payload.length < 126) {
      header.push(0x80 | payload.length);
    } else if (payload.length <= 0xffff) {
      header.push(0x80 | 126, (payload.length >>> 8) & 0xff, payload.length & 0xff);
    } else {
      const len = BigInt(payload.length);
      header.push(0x80 | 127);
      const lengthBytes = Buffer.alloc(8);
      lengthBytes.writeBigUInt64BE(len);
      header.push(...lengthBytes);
    }
    const masked = Buffer.alloc(payload.length);
    for (let i = 0; i < payload.length; i += 1) masked[i] = payload[i] ^ mask[i % 4];
    this.socket.write(Buffer.concat([Buffer.from(header), mask, masked]));
  }

  private feed(chunk: Buffer) {
    this.buffer = this.buffer.length ? Buffer.concat([this.buffer, chunk]) : chunk;
    while (this.tryFrame()) { /* drain */ }
  }

  private tryFrame() {
    if (this.buffer.length < 2) return false;
    const b0 = this.buffer[0];
    const b1 = this.buffer[1];
    const fin = Boolean(b0 & 0x80);
    const rsv = b0 & 0x70;
    const opcode = b0 & 0x0f;
    const masked = Boolean(b1 & 0x80);
    if (rsv) return this.protocolError(`UNSUPPORTED_RSV_BITS:${rsv}`);

    let offset = 2;
    let length = b1 & 0x7f;
    if (length === 126) {
      if (this.buffer.length < offset + 2) return false;
      length = this.buffer.readUInt16BE(offset);
      offset += 2;
    } else if (length === 127) {
      if (this.buffer.length < offset + 8) return false;
      const big = this.buffer.readBigUInt64BE(offset);
      if (big > BigInt(Number.MAX_SAFE_INTEGER)) return this.protocolError("FRAME_TOO_LARGE");
      length = Number(big);
      offset += 8;
    }

    let mask: Buffer | null = null;
    if (masked) {
      if (this.buffer.length < offset + 4) return false;
      mask = this.buffer.subarray(offset, offset + 4);
      offset += 4;
    }
    if (this.buffer.length < offset + length) return false;
    let payload = Buffer.from(this.buffer.subarray(offset, offset + length));
    this.buffer = this.buffer.subarray(offset + length);
    if (mask) {
      for (let i = 0; i < payload.length; i += 1) payload[i] ^= mask[i % 4];
    }

    if (opcode >= 0x8 && (!fin || payload.length > 125)) {
      return this.protocolError("BAD_CONTROL_FRAME");
    }
    this.handleFrame(opcode, fin, payload);
    return true;
  }

  private handleFrame(opcode: number, fin: boolean, payload: Buffer) {
    if (opcode === 0x9) {
      this.sendFrame(0xA, payload);
      return;
    }
    if (opcode === 0xA) return;
    if (opcode === 0x8) {
      const code = payload.length >= 2 ? payload.readUInt16BE(0) : 1000;
      const reason = payload.length > 2 ? payload.subarray(2).toString("utf8") : "";
      if (!this.closing) {
        this.closing = true;
        try { this.sendFrame(0x8, payload); } catch { /* socket may be gone */ }
      }
      this.socket?.end();
      this.emit("close", code, reason);
      return;
    }
    if (opcode === 0x2) {
      this.protocolError("BINARY_FRAME_UNSUPPORTED");
      return;
    }
    if (opcode === 0x1) {
      if (this.fragmentedOpcode !== null) {
        this.protocolError("NEW_TEXT_DURING_FRAGMENT");
        return;
      }
      if (fin) this.emit("message", payload.toString("utf8"));
      else {
        this.fragmentedOpcode = opcode;
        this.fragments = [payload];
      }
      return;
    }
    if (opcode === 0x0) {
      if (this.fragmentedOpcode === null) {
        this.protocolError("ORPHAN_CONTINUATION");
        return;
      }
      this.fragments.push(payload);
      if (fin) {
        const message = Buffer.concat(this.fragments).toString("utf8");
        this.fragments = [];
        this.fragmentedOpcode = null;
        this.emit("message", message);
      }
      return;
    }
    this.protocolError(`UNSUPPORTED_OPCODE:${opcode}`);
  }

  private protocolError(reason: string): false {
    const error = new Error(`WEBSOCKET_PROTOCOL_ERROR:${reason}`);
    this.emit("error", error);
    this.close(1002, reason);
    return false;
  }
}

export function parseKalshiEnvelope(text: string): KalshiWsEnvelope {
  const parsed = JSON.parse(text) as KalshiWsEnvelope;
  if (!parsed || typeof parsed !== "object") throw new Error("BAD_WS_JSON_ENVELOPE");
  return parsed;
}

export function marketTickerOf(envelope: KalshiWsEnvelope) {
  const ticker = envelope.msg?.market_ticker;
  return typeof ticker === "string" ? ticker : null;
}

/**
 * With use_yes_price=true, NO-side orderbook prices arrive on the YES-leg price
 * scale. Convert them back to native NO bid prices before mutating our internal
 * binary book. YES-side levels are unchanged.
 */
export function normalizeNativePrice(side: "yes" | "no", rawPrice: string | number, useYesPrice: boolean) {
  const fp = parsePriceFp(rawPrice);
  return side === "no" && useYesPrice ? formatPriceFp(complementPriceFp(fp)) : formatPriceFp(fp);
}

export function normalizeOrderbookSnapshot(envelope: KalshiWsEnvelope, useYesPrice: boolean) {
  if (envelope.type !== "orderbook_snapshot" || !envelope.msg) throw new Error("NOT_ORDERBOOK_SNAPSHOT");
  const yesRaw = envelope.msg.yes_dollars_fp;
  const noRaw = envelope.msg.no_dollars_fp;
  if (!Array.isArray(yesRaw) || !Array.isArray(noRaw)) throw new Error("BAD_ORDERBOOK_SNAPSHOT_LEVELS");
  const pair = (entry: unknown): [string | number, string | number] => {
    if (!Array.isArray(entry) || entry.length < 2) throw new Error("BAD_ORDERBOOK_LEVEL");
    return [entry[0] as string | number, entry[1] as string | number];
  };
  return {
    marketTicker: String(envelope.msg.market_ticker ?? ""),
    sid: Number(envelope.sid),
    seq: Number(envelope.seq),
    exchangeTsMs: numberOrNull(envelope.msg.ts_ms),
    yes: yesRaw.map(pair),
    no: noRaw.map(pair).map(([p, q]) => [normalizeNativePrice("no", p, useYesPrice), q] as [string, string | number]),
  };
}

export function normalizeOrderbookDelta(envelope: KalshiWsEnvelope, useYesPrice: boolean) {
  if (envelope.type !== "orderbook_delta" || !envelope.msg) throw new Error("NOT_ORDERBOOK_DELTA");
  const side = envelope.msg.side;
  if (side !== "yes" && side !== "no") throw new Error(`BAD_ORDERBOOK_SIDE:${String(side)}`);
  return {
    marketTicker: String(envelope.msg.market_ticker ?? ""),
    sid: Number(envelope.sid),
    seq: Number(envelope.seq),
    exchangeTsMs: numberOrNull(envelope.msg.ts_ms),
    side,
    price: normalizeNativePrice(side, String(envelope.msg.price_dollars ?? ""), useYesPrice),
    delta: String(envelope.msg.delta_fp ?? ""),
  };
}

function numberOrNull(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
