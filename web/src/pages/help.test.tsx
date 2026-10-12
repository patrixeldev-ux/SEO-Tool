import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownLite, parseBlocks } from "@/components/markdown-lite";

describe("Google setup guide", () => {
  it("the in-app copy is identical to docs/GOOGLE_SETUP.md", () => {
    const docs = readFileSync(resolve(__dirname, "../../../docs/GOOGLE_SETUP.md"), "utf8");
    const inApp = readFileSync(resolve(__dirname, "../content/google-setup.md"), "utf8");
    expect(inApp).toBe(docs);
  });

  it("names the exact redirect address, the env variables and both APIs to enable", () => {
    const docs = readFileSync(resolve(__dirname, "../content/google-setup.md"), "utf8");
    for (const text of [
      "http://localhost:8000/integrations/google/callback",
      "GOOGLE_CLIENT_ID",
      "GOOGLE_CLIENT_SECRET",
      "Google Search Console API",
      "PageSpeed Insights API",
      "Web application",
      "scripts\\dev-up.ps1",
      "Generate API Key",
    ]) {
      expect(docs).toContain(text);
    }
  });
});

describe("markdown renderer", () => {
  const SAMPLE = [
    "# Title",
    "",
    "Some **bold** text, `code` and a [link](https://example.com).",
    "",
    "1. First step",
    "2. Second step",
    "",
    "   `indented code`",
    "3. Third step",
    "",
    "- a bullet",
    "",
    "> a note",
    "",
    "| A | B |",
    "|---|---|",
    "| 1 | 2 |",
  ].join("\n");

  it("parses headings, lists with continuation paragraphs, quotes and tables", () => {
    const blocks = parseBlocks(SAMPLE);
    expect(blocks.map((b) => b.kind)).toEqual(["h", "p", "list", "list", "quote", "table"]);
    const steps = blocks[2];
    expect(steps?.kind === "list" && steps.items).toEqual([["First step"], ["Second step", "`indented code`"], ["Third step"]]);
  });

  it("renders text as elements and never as HTML", () => {
    render(<MarkdownLite source={"# Hi\n\nA <script>alert(1)</script> [x](https://example.com) **b**"} />);
    expect(screen.getByRole("heading", { name: "Hi" })).toBeInTheDocument();
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByRole("link", { name: "x" })).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("ignores non-http links", () => {
    render(<MarkdownLite source={"[bad](javascript:alert(1))"} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
