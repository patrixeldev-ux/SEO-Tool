import { describe, expect, it } from "vitest";
import { jsonLdBlocksToDrop, needsPrescan, rewrite } from "../src/rewrite";
import type { Op } from "../src/types";

const URL_ = "https://shop.example/page";
const PAGE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Old title</title>
<meta name="description" content="Old description">
<link rel="canonical" href="https://shop.example/page">
</head>
<body><h1>Coffee grinders</h1>
<p>We sell coffee grinders and fresh beans for home brewing.</p>
<p>Read more about espresso machines in our guide.</p>
<img src="/logo.png"></body></html>
`;

async function run(ops: Op[], html = PAGE): Promise<string> {
  const base = new Response(html, { headers: { "content-type": "text/html" } });
  const drop = needsPrescan(ops) ? jsonLdBlocksToDrop(html, ops) : new Set<number>();
  return rewrite(base, ops, URL_, drop).text();
}

describe("title, meta description, canonical", () => {
  it("replaces an existing title and escapes it", async () => {
    const out = await run([{ op: "title", value: "Tom & <Jerry>" }]);
    expect(out).toContain("<title>Tom &amp; &lt;Jerry&gt;</title>");
    expect(out).not.toContain("Old title");
  });

  it("inserts a title when the page has none", async () => {
    const out = await run([{ op: "title", value: "New" }], "<html><head></head><body>x</body></html>");
    expect(out).toContain("<title>New</title>\n</head>");
  });

  it("sets and inserts the meta description", async () => {
    expect(await run([{ op: "meta_description", value: 'A "new" one' }])).toContain(
      'content="A &quot;new&quot; one"',
    );
    const out = await run([{ op: "meta_description", value: "Hello" }], "<html><head><title>t</title></head></html>");
    expect(out).toContain('<meta name="description" content="Hello">');
  });

  it("sets and inserts the canonical", async () => {
    expect(await run([{ op: "canonical", value: "https://shop.example/new" }])).toContain(
      'href="https://shop.example/new"',
    );
    const out = await run([{ op: "canonical", value: "https://a.example/x" }], "<html><head></head></html>");
    expect(out).toContain('<link rel="canonical" href="https://a.example/x">');
  });

  it("changes only the first of two titles and the last op wins", async () => {
    const out = await run(
      [
        { op: "title", value: "First" },
        { op: "title", value: "Second" },
      ],
      "<html><head><title>a</title><title>b</title></head></html>",
    );
    expect(out).toContain("<title>Second</title><title>b</title>");
  });
});

describe("image alt", () => {
  it("sets alt by resolved src, relative or absolute", async () => {
    const out = await run([{ op: "alt", src: "https://shop.example/logo.png", value: "Shop logo" }]);
    expect(out).toContain('alt="Shop logo"');
  });
  it("leaves other images alone", async () => {
    const out = await run([{ op: "alt", src: "https://shop.example/other.png", value: "X" }]);
    expect(out).not.toContain('alt="X"');
  });
});

describe("JSON-LD", () => {
  const org = { "@context": "https://schema.org", "@type": "Organization", name: "Shop" };
  it("appends a block before </head> and escapes '<'", async () => {
    const out = await run([
      { op: "jsonld", id: "searchos-aa", value: { ...org, name: "A</script>B" }, replace: null },
    ]);
    expect(out).toContain('<script type="application/ld+json" id="searchos-aa">');
    expect(out).not.toContain("A</script>B");
    expect(out).toContain("\\u003c/script>");
    expect(out.indexOf("searchos-aa")).toBeLessThan(out.indexOf("</head>"));
  });

  it("replaces the existing block of the same type but keeps others", async () => {
    const html =
      '<html><head><script type="application/ld+json">{"@type":"Organization","name":"Old"}</script>' +
      '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script></head><body></body></html>';
    const out = await run(
      [{ op: "jsonld", id: "searchos-bb", value: org, replace: { types: ["Organization", "Corporation"] } }],
      html,
    );
    expect(out).not.toContain('"name":"Old"');
    expect(out).toContain("BreadcrumbList");
    expect(out).toContain("searchos-bb");
  });

  it("replaces a block that does not parse when asked to", async () => {
    const html = '<html><head><script type="application/ld+json">{oops</script></head></html>';
    const out = await run([{ op: "jsonld", id: "searchos-cc", value: org, replace: "invalid" }], html);
    expect(out).not.toContain("{oops");
    expect(out).toContain("searchos-cc");
  });

  it("is idempotent for its own block", async () => {
    const once = await run([{ op: "jsonld", id: "searchos-dd", value: org, replace: null }]);
    const twice = await run([{ op: "jsonld", id: "searchos-dd", value: org, replace: null }], once);
    expect(twice.match(/searchos-dd/g)?.length).toBe(1);
  });
});

describe("internal links", () => {
  it("wraps the first exact anchor occurrence in a paragraph", async () => {
    const out = await run([{ op: "link", id: "l1", anchor: "espresso machines", to: "https://shop.example/espresso" }]);
    expect(out).toContain(
      'about <a href="https://shop.example/espresso" data-searchos="l1">espresso machines</a> in our guide',
    );
  });

  it("only the first occurrence", async () => {
    const out = await run(
      [{ op: "link", id: "l2", anchor: "coffee", to: "https://shop.example/c" }],
      "<html><body><p>coffee and coffee</p></body></html>",
    );
    expect(out.match(/<a /g)?.length).toBe(1);
    expect(out).toContain('<a href="https://shop.example/c" data-searchos="l2">coffee</a> and coffee');
  });

  it("skips headings, existing links, navigation and code", async () => {
    const html =
      "<html><body><h2>Coffee grinders</h2><nav><p>Coffee grinders</p></nav>" +
      '<p><a href="/x">Coffee grinders</a></p><p><code>Coffee grinders</code></p></body></html>';
    const out = await run([{ op: "link", id: "l3", anchor: "Coffee grinders", to: "https://shop.example/g" }], html);
    expect(out).not.toContain("data-searchos");
  });

  it("does nothing when the anchor text is not found", async () => {
    const out = await run([{ op: "link", id: "l4", anchor: "tea kettles", to: "https://shop.example/t" }]);
    expect(out).toBe(PAGE);
  });

  it("puts several links into one text node", async () => {
    const out = await run(
      [
        { op: "link", id: "a", anchor: "grinders", to: "https://shop.example/g" },
        { op: "link", id: "b", anchor: "beans", to: "https://shop.example/b" },
      ],
      "<html><body><p>We sell grinders and beans.</p></body></html>",
    );
    expect(out).toContain(
      '<p>We sell <a href="https://shop.example/g" data-searchos="a">grinders</a> and <a href="https://shop.example/b" data-searchos="b">beans</a>.</p>',
    );
  });

  it("works when the anchor spans a large text node (chunk boundaries)", async () => {
    const filler = "lorem ipsum ".repeat(5000);
    const out = await run(
      [{ op: "link", id: "big", anchor: "needle text", to: "https://shop.example/n" }],
      `<html><body><p>${filler}needle text${filler}</p></body></html>`,
    );
    expect(out).toContain('<a href="https://shop.example/n" data-searchos="big">needle text</a>');
    expect(out.length).toBeGreaterThan(filler.length * 2);
  });
});

describe("everything else stays byte for byte", () => {
  it("an untouched page is returned unchanged by a no-op op set", async () => {
    const out = await run([{ op: "alt", src: "/missing.png", value: "x" }]);
    expect(out).toBe(PAGE);
  });
});
