import { describe, expect, it } from "vitest";

import {
  confidenceText,
  diffWords,
  editorText,
  gainText,
  parseEditorText,
  problemsOf,
  valueText,
} from "@/lib/action-utils";

const join = (parts: { kind: string; text: string }[], kinds: string[]) =>
  parts
    .filter((p) => kinds.includes(p.kind))
    .map((p) => p.text)
    .join("");

describe("diffWords", () => {
  it("marks removed and added words and keeps both texts intact", () => {
    const parts = diffWords("Buy cheap coffee beans online", "Buy fresh coffee beans today");
    expect(join(parts, ["same", "del"])).toBe("Buy cheap coffee beans online");
    expect(join(parts, ["same", "add"])).toBe("Buy fresh coffee beans today");
    expect(parts.filter((p) => p.kind === "del").map((p) => p.text.trim())).toEqual(["cheap", "online"]);
    expect(parts.filter((p) => p.kind === "add").map((p) => p.text.trim())).toEqual(["fresh", "today"]);
  });

  it("handles empty sides and identical text", () => {
    expect(diffWords("", "New title")).toEqual([{ kind: "add", text: "New title" }]);
    expect(diffWords("Old", "")).toEqual([{ kind: "del", text: "Old" }]);
    expect(diffWords("Same words", "Same words")).toEqual([{ kind: "same", text: "Same words" }]);
  });

  it("falls back to whole-text replacement for very long text", () => {
    const long = "word ".repeat(2000);
    const parts = diffWords(long, `${long}more`);
    expect(parts.map((p) => p.kind)).toEqual(["del", "add"]);
  });
});

describe("valueText", () => {
  it("renders every kind of value readably", () => {
    expect(valueText("rewrite_title", "A title")).toBe("A title");
    expect(valueText("rewrite_title", null)).toBe("");
    expect(valueText("add_internal_links", { links: [{ to: "https://s.example/b", anchor: "burr grinder" }] })).toBe(
      '"burr grinder" → https://s.example/b',
    );
    expect(valueText("collapse_redirect", { from: "https://s.example/a", to: "https://s.example/b", status: 301 })).toBe(
      "301 redirect: https://s.example/a → https://s.example/b",
    );
    expect(valueText("fix_link", { from: "https://s.example/x", mode: "remove" })).toBe("Remove the link to https://s.example/x");
    expect(valueText("refresh_content", { kind: "brief", steps: ["One", "Two"] })).toBe("1. One\n2. Two");
    expect(valueText("add_jsonld", { "@type": "Organization" })).toContain('"@type": "Organization"');
  });
});

describe("editor", () => {
  it("edits strings as text and structures as JSON", () => {
    expect(editorText({ type: "rewrite_title", proposed_value: "T", current_value: "x" })).toBe("T");
    expect(editorText({ type: "add_jsonld", proposed_value: { a: 1 }, current_value: null })).toBe('{\n  "a": 1\n}');
    expect(parseEditorText("rewrite_title", "  My title ")).toEqual({ ok: true, value: "  My title " });
    expect(parseEditorText("collapse_redirect", '{"to": "x"}')).toEqual({ ok: true, value: { to: "x" } });
    const bad = parseEditorText("fix_link", "{oops");
    expect(bad.ok).toBe(false);
  });

  it("reads validator problems from an API error", () => {
    expect(problemsOf({ detail: { message: "x", problems: ["Too short.", "No."] } })).toEqual(["Too short.", "No."]);
    expect(problemsOf(new Error("plain"))).toEqual([]);
    expect(problemsOf(null)).toEqual([]);
  });
});

describe("numbers", () => {
  it("words the estimates plainly", () => {
    expect(gainText(0)).toBe("no clicks estimated");
    expect(gainText(0.4)).toBe("<1 click / 28 days");
    expect(gainText(3)).toBe("+3.0 clicks / 28 days");
    expect(gainText(34)).toBe("+34 clicks / 28 days");
    expect(gainText(256.4)).toBe("+256 clicks / 28 days");
    expect(confidenceText(0.2)).toBe("low");
    expect(confidenceText(0.4)).toBe("medium");
    expect(confidenceText(0.8)).toBe("high");
  });
});
