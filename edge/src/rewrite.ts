import type { Op } from "./types";

/** Elements whose text we never touch when inserting a link (same list as backend/searchos/deploy/htmlpatch.py). */
const FORBIDDEN =
  "a, script, style, h1, h2, h3, h4, h5, h6, button, select, textarea, code, pre, nav, header, footer, aside, form, option, label";
/** Elements whose own text may receive an internal link. */
const ALLOWED = "p, li, td, dd, blockquote, figcaption";

const LD_BLOCK = /<script\b[^>]*type\s*=\s*["']?application\/ld\+json["']?[^>]*>([\s\S]*?)<\/script\s*>/gi;

export function escapeText(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function escapeAttr(s: string): string {
  return escapeText(s).replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function scriptJson(value: unknown): string {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}

function unescapeBasic(s: string): string {
  return s
    .replace(/&quot;/g, '"')
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function entityTypes(data: unknown): string[] {
  const items = Array.isArray(data) ? data : [data];
  const out: string[] = [];
  for (const item of items) {
    if (typeof item !== "object" || item === null) continue;
    const rec = item as Record<string, unknown>;
    const nodes = Array.isArray(rec["@graph"]) ? (rec["@graph"] as unknown[]) : [rec];
    for (const node of nodes) {
      if (typeof node !== "object" || node === null) continue;
      const t = (node as Record<string, unknown>)["@type"];
      for (const x of Array.isArray(t) ? t : [t]) if (x) out.push(String(x).split("/").pop()!);
    }
  }
  return out;
}

export function needsPrescan(ops: Op[]): boolean {
  return ops.some((o) => o.op === "jsonld" && o.replace !== null && o.replace !== undefined);
}

/**
 * HTMLRewriter streams, so it cannot decide to delete a JSON-LD block after reading its content. For the few
 * pages that replace existing structured data, the raw HTML is scanned first and the positions (0, 1, 2, ...) of
 * the blocks to delete are handed to the rewriter.
 */
export function jsonLdBlocksToDrop(html: string, ops: Op[]): Set<number> {
  const drop = new Set<number>();
  const rules = ops.flatMap((o) => (o.op === "jsonld" && o.replace ? [o.replace] : []));
  if (rules.length === 0) return drop;
  let index = 0;
  for (const m of html.matchAll(LD_BLOCK)) {
    let data: unknown;
    let valid = true;
    try {
      data = JSON.parse(unescapeBasic(m[1] ?? ""));
    } catch {
      valid = false;
    }
    for (const rule of rules) {
      if (!valid) {
        if (rule === "invalid") drop.add(index);
      } else if (typeof rule === "object") {
        const types = new Set(entityTypes(data));
        if (rule.types.some((t) => types.has(t))) drop.add(index);
      }
    }
    index++;
  }
  return drop;
}

type Segment = { html: boolean; s: string };


function resolve(src: string, base: string): string {
  try {
    return new URL(src, base).href;
  } catch {
    return src;
  }
}

/** Applies page operations to an HTML response. The body is streamed; nothing is buffered except one text node. */
export function rewrite(response: Response, ops: Op[], pageUrl: string, dropLd: Set<number> = new Set()): Response {
  const last = <T extends Op["op"]>(kind: T) =>
    [...ops].reverse().find((o): o is Extract<Op, { op: T }> => o.op === kind);
  const title = last("title");
  const description = last("meta_description");
  const canonical = last("canonical");
  const alts = ops.filter((o): o is Extract<Op, { op: "alt" }> => o.op === "alt");
  const jsonlds = ops.filter((o): o is Extract<Op, { op: "jsonld" }> => o.op === "jsonld");
  const links = ops
    .filter((o): o is Extract<Op, { op: "link" }> => o.op === "link")
    .map((o) => ({ op: o, done: false }));
  const ownIds = new Set(jsonlds.map((o) => o.id));
  const altBySrc = new Map<string, string>();
  for (const a of alts) altBySrc.set(resolve(a.src, pageUrl).replace(/\/+$/, ""), a.value);

  let seenTitle = false;
  let seenDescription = false;
  let seenCanonical = false;
  let ldIndex = 0;
  let forbiddenDepth = 0;
  let allowedDepth = 0;
  let pending = "";

  const rw = new HTMLRewriter();

  if (title) {
    rw.on("title", {
      element(el) {
        if (seenTitle) return;
        seenTitle = true;
        el.setInnerContent(title.value);
      },
    });
  }
  if (description) {
    rw.on('meta[name="description" i]', {
      element(el) {
        if (seenDescription) return;
        seenDescription = true;
        el.setAttribute("content", description.value);
      },
    });
  }
  if (canonical) {
    rw.on('link[rel~="canonical" i]', {
      element(el) {
        if (seenCanonical) return;
        seenCanonical = true;
        el.setAttribute("href", canonical.value);
      },
    });
  }
  if (altBySrc.size > 0) {
    rw.on("img", {
      element(el) {
        const src = el.getAttribute("src");
        if (!src) return;
        const value = altBySrc.get(resolve(src, pageUrl).replace(/\/+$/, ""));
        if (value !== undefined) el.setAttribute("alt", value);
      },
    });
  }
  if (jsonlds.length > 0 || dropLd.size > 0) {
    rw.on('script[type="application/ld+json" i]', {
      element(el) {
        const i = ldIndex++;
        const id = el.getAttribute("id");
        if ((id && ownIds.has(id)) || dropLd.has(i)) el.remove();
      },
    });
  }
  const headInsert = () => {
    let out = "";
    if (title && !seenTitle) out += `<title>${escapeText(title.value)}</title>\n`;
    if (description && !seenDescription)
      out += `<meta name="description" content="${escapeAttr(description.value)}">\n`;
    if (canonical && !seenCanonical) out += `<link rel="canonical" href="${escapeAttr(canonical.value)}">\n`;
    for (const j of jsonlds)
      out += `<script type="application/ld+json" id="${escapeAttr(j.id)}">${scriptJson(j.value)}</script>\n`;
    return out;
  };
  if (title || description || canonical || jsonlds.length > 0) {
    rw.on("head", {
      element(el) {
        el.onEndTag((end) => {
          const html = headInsert();
          if (html) end.before(html, { html: true });
        });
      },
    });
  }
  if (links.length > 0) {
    rw.on(FORBIDDEN, {
      element(el) {
        forbiddenDepth++;
        el.onEndTag(() => {
          forbiddenDepth = Math.max(0, forbiddenDepth - 1);
        });
      },
    });
    rw.on(ALLOWED, {
      element(el) {
        allowedDepth++;
        el.onEndTag(() => {
          allowedDepth = Math.max(0, allowedDepth - 1);
        });
      },
    });
    rw.onDocument({
      text(t) {
        if (forbiddenDepth > 0 || allowedDepth === 0 || links.every((l) => l.done)) return;
        pending += t.text;
        if (!t.lastInTextNode) {
          t.remove();
          return;
        }
        const full = pending;
        pending = "";
        let segments: Segment[] = [{ html: false, s: full }];
        for (const l of links) {
          if (l.done) continue;
          for (let i = 0; i < segments.length; i++) {
            const seg = segments[i]!;
            if (seg.html) continue;
            const at = seg.s.indexOf(l.op.anchor);
            if (at < 0) continue;
            const a = `<a href="${escapeAttr(l.op.to)}" data-searchos="${escapeAttr(l.op.id)}">${escapeText(l.op.anchor)}</a>`;
            segments.splice(
              i,
              1,
              { html: false, s: seg.s.slice(0, at) },
              { html: true, s: a },
              { html: false, s: seg.s.slice(at + l.op.anchor.length) },
            );
            l.done = true;
            break;
          }
        }
        segments = segments.filter((s) => s.s !== "");
        t.replace(segments.map((s) => s.s).join(""), { html: true });
      },
    });
  }

  const out = rw.transform(response);
  out.headers.delete("content-length");
  return out;
}

