import type { Op, Rules } from "./types";

export const MAX_AGE_SECONDS = 24 * 3600;
export const CLOCK_SKEW_SECONDS = 300;

const enc = new TextEncoder();

function pct(s: string): string {
  // Python's urllib.parse.quote(s, safe="-_.~"): encodeURIComponent keeps !'()* too, so encode those as well.
  return encodeURIComponent(s).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
}

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/** Same function as `normalise_path` in backend/searchos/deploy/signing.py (shared test vectors). */
export function normalisePath(pathname: string): string {
  const raw = pathname.split("?")[0]!.split("#")[0]! || "/";
  let path = raw
    .split("/")
    .map((seg) => pct(safeDecode(seg)))
    .join("/");
  if (!path.startsWith("/")) path = "/" + path;
  if (path.length > 1 && path.endsWith("/")) path = path.replace(/\/+$/, "") || "/";
  return path;
}

function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sign(body: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
  ]);
  return hex(await crypto.subtle.sign("HMAC", key, enc.encode(body)));
}

function equalStrings(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** The rules if the signature matches and they are at most 24 hours old; otherwise null (never applied). */
export async function parseRules(
  body: string,
  signature: string | null,
  secret: string,
  nowSeconds: number,
): Promise<Rules | null> {
  if (!signature || !secret) return null;
  if (!equalStrings(await sign(body, secret), signature.trim().toLowerCase())) return null;
  let data: unknown;
  try {
    data = JSON.parse(body);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null) return null;
  const r = data as Partial<Rules>;
  if (typeof r.issued_at !== "number" || typeof r.host !== "string" || typeof r.pages !== "object" || !r.pages) {
    return null;
  }
  if (nowSeconds - r.issued_at > MAX_AGE_SECONDS) return null;
  if (r.issued_at - nowSeconds > CLOCK_SKEW_SECONDS) return null;
  return { v: Number(r.v ?? 0), issued_at: r.issued_at, enabled: r.enabled !== false, host: r.host, pages: r.pages as Record<string, Op[]> };
}
