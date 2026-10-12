import { Fragment, type ReactNode } from "react";

/**
 * A tiny Markdown renderer for the bundled help guide: headings, paragraphs, ordered and bullet lists (with
 * indented continuation paragraphs), block quotes, simple tables, and **bold**, `code` and [links](https://...).
 * It builds React elements, never HTML strings, so nothing in the text can inject markup.
 */
type Block =
  | { kind: "h"; level: 1 | 2 | 3; text: string }
  | { kind: "p"; text: string }
  | { kind: "quote"; text: string }
  | { kind: "list"; ordered: boolean; items: string[][] }
  | { kind: "table"; header: string[]; rows: string[][] };

const INLINE = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;

export function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(INLINE)) {
    const index = match.index ?? 0;
    if (index > last) out.push(text.slice(last, index));
    if (match[1] !== undefined) out.push(<strong key={key++}>{renderInline(match[1])}</strong>);
    else if (match[2] !== undefined)
      out.push(
        <code key={key++} className="rounded bg-muted px-1 py-0.5 text-[0.85em]">
          {match[2]}
        </code>,
      );
    else
      out.push(
        <a key={key++} href={match[4]} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">
          {match[3]}
        </a>,
      );
    last = index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

export function parseBlocks(markdown: string): Block[] {
  const lines = markdown.replaceAll("\r\n", "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (line.trim() === "") {
      i++;
      continue;
    }
    const heading = /^(#{1,3}) (.*)$/.exec(line);
    if (heading) {
      blocks.push({ kind: "h", level: heading[1]!.length as 1 | 2 | 3, text: heading[2]! });
      i++;
      continue;
    }
    if (line.startsWith(">")) {
      const parts: string[] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith(">")) {
        parts.push((lines[i] ?? "").replace(/^>\s?/, ""));
        i++;
      }
      blocks.push({ kind: "quote", text: parts.join(" ").trim() });
      continue;
    }
    if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith("|")) {
        rows.push(splitRow(lines[i] ?? ""));
        i++;
      }
      const [header = [], , ...body] = rows; // rows[1] is the |---|---| separator
      blocks.push({ kind: "table", header, rows: body });
      continue;
    }
    const listStart = /^(\d+\.|-) (.*)$/.exec(line);
    if (listStart) {
      const ordered = listStart[1] !== "-";
      const items: string[][] = [];
      while (i < lines.length) {
        const current = lines[i] ?? "";
        const item = /^(\d+\.|-) (.*)$/.exec(current);
        if (item && (item[1] !== "-") === ordered) {
          items.push([item[2]!]);
          i++;
        } else if (current.trim() === "") {
          // A blank line continues the list only if an indented paragraph or another item follows.
          let j = i;
          while (j < lines.length && (lines[j] ?? "").trim() === "") j++;
          const next = lines[j] ?? "";
          if (/^\s{2,}\S/.test(next) && items.length > 0) {
            items[items.length - 1]!.push(next.trim());
            i = j + 1;
          } else if (/^(\d+\.|-) /.test(next) && (next.startsWith("-") !== ordered)) {
            i = j;
          } else break;
        } else if (/^\s{2,}\S/.test(current) && items.length > 0) {
          items[items.length - 1]!.push(current.trim());
          i++;
        } else break;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    const parts: string[] = [];
    while (
      i < lines.length &&
      (lines[i] ?? "").trim() !== "" &&
      !/^(#{1,3} |>|\||\d+\. |- )/.test(lines[i] ?? "")
    ) {
      parts.push((lines[i] ?? "").trim());
      i++;
    }
    blocks.push({ kind: "p", text: parts.join(" ") });
  }
  return blocks;
}

export function MarkdownLite({ source }: { source: string }) {
  return (
    <div className="space-y-4 text-sm leading-relaxed">
      {parseBlocks(source).map((block, index) => {
        switch (block.kind) {
          case "h":
            if (block.level === 1) return <h1 key={index} className="text-2xl font-semibold tracking-tight">{renderInline(block.text)}</h1>;
            if (block.level === 2) return <h2 key={index} className="mt-8 border-b pb-1 text-lg font-semibold">{renderInline(block.text)}</h2>;
            return <h3 key={index} className="font-semibold">{renderInline(block.text)}</h3>;
          case "p":
            return <p key={index}>{renderInline(block.text)}</p>;
          case "quote":
            return (
              <blockquote key={index} className="rounded-lg border-l-4 border-primary/60 bg-muted/50 px-4 py-3 text-muted-foreground">
                {renderInline(block.text)}
              </blockquote>
            );
          case "list": {
            const Tag = block.ordered ? "ol" : "ul";
            return (
              <Tag key={index} className={`space-y-2 pl-6 ${block.ordered ? "list-decimal" : "list-disc"}`}>
                {block.items.map((paragraphs, n) => (
                  <li key={n} className="space-y-1.5 pl-1">
                    {paragraphs.map((text, p) => (
                      <Fragment key={p}>{p === 0 ? renderInline(text) : <span className="block">{renderInline(text)}</span>}</Fragment>
                    ))}
                  </li>
                ))}
              </Tag>
            );
          }
          case "table":
            return (
              <div key={index} className="overflow-x-auto rounded-lg border">
                <table className="w-full text-left text-sm">
                  <thead className="border-b bg-muted/50">
                    <tr>
                      {block.header.map((cell, n) => (
                        <th key={n} className="px-3 py-2 font-medium">{renderInline(cell)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {block.rows.map((row, r) => (
                      <tr key={r}>
                        {row.map((cell, n) => (
                          <td key={n} className="px-3 py-2 align-top">{renderInline(cell)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
        }
      })}
    </div>
  );
}
