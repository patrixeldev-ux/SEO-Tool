import { describe, expect, it } from "vitest";
import vectors from "./vectors.json";
import { MAX_AGE_SECONDS, normalisePath, parseRules, sign } from "../src/rules";

describe("shared vectors with the Python side", () => {
  it.each(vectors.normalise)("normalises $input", ({ input, path }) => {
    expect(normalisePath(input)).toBe(path);
  });

  it("produces the same HMAC as Python", async () => {
    const { secret, body, sig } = vectors.signature;
    expect(await sign(body, secret)).toBe(sig);
  });
});

describe("parseRules", () => {
  const secret = "s3cret";
  const issued = 1_760_000_000;
  const body = (over: Record<string, unknown> = {}) =>
    JSON.stringify({ v: 1, issued_at: issued, enabled: true, host: "shop.example", pages: {}, ...over });

  it("accepts a correctly signed, fresh body", async () => {
    const b = body();
    const r = await parseRules(b, await sign(b, secret), secret, issued + 60);
    expect(r?.host).toBe("shop.example");
  });

  it("rejects an unsigned body", async () => {
    expect(await parseRules(body(), null, secret, issued)).toBeNull();
    expect(await parseRules(body(), "", secret, issued)).toBeNull();
  });

  it("rejects a wrong signature and a tampered body", async () => {
    const b = body();
    const sig = await sign(b, secret);
    expect(await parseRules(b, sig, "other-secret", issued)).toBeNull();
    expect(await parseRules(body({ host: "evil.example" }), sig, secret, issued)).toBeNull();
    expect(await parseRules(b, sig.replace(/.$/, sig.endsWith("0") ? "1" : "0"), secret, issued)).toBeNull();
  });

  it("rejects rules older than 24 hours and accepts exactly 24 hours", async () => {
    const b = body();
    const sig = await sign(b, secret);
    expect(await parseRules(b, sig, secret, issued + MAX_AGE_SECONDS)).not.toBeNull();
    expect(await parseRules(b, sig, secret, issued + MAX_AGE_SECONDS + 1)).toBeNull();
  });

  it("rejects rules issued in the future beyond the clock-skew allowance", async () => {
    const b = body();
    const sig = await sign(b, secret);
    expect(await parseRules(b, sig, secret, issued - 301)).toBeNull();
    expect(await parseRules(b, sig, secret, issued - 100)).not.toBeNull();
  });

  it("rejects signed garbage", async () => {
    const b = "not json";
    expect(await parseRules(b, await sign(b, secret), secret, issued)).toBeNull();
    const c = JSON.stringify({ hello: 1 });
    expect(await parseRules(c, await sign(c, secret), secret, issued)).toBeNull();
  });
});
