import { describe, expect, it } from "vitest";
import cases from "./parity-cases.json";
import { jsonLdBlocksToDrop, needsPrescan, rewrite } from "../src/rewrite";
import type { Op } from "../src/types";

// The same cases are run against backend/searchos/deploy/htmlpatch.py (tests/test_deploy_htmlpatch_parity.py),
// so the Worker, the GitHub static-HTML editor and the verification code agree on what each operation does.
describe("shared operation cases", () => {
  it.each(cases)("$name", async (c) => {
    const ops = c.ops as unknown as Op[];
    const drop = needsPrescan(ops) ? jsonLdBlocksToDrop(c.html, ops) : new Set<number>();
    const out = await rewrite(new Response(c.html, { headers: { "content-type": "text/html" } }), ops, c.url, drop).text();
    for (const s of c.contains) expect(out).toContain(s);
    for (const s of c.absent) expect(out).not.toContain(s);
  });
});
