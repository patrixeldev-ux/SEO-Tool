import { diffWords, valueText } from "@/lib/action-utils";
import { cn } from "@/lib/utils";

/** Current and proposed value side by side; removed words struck through on the left, added words highlighted on the right. */
export function DiffView({ type, before, after }: { type: string; before: unknown; after: unknown }) {
  const was = valueText(type, before);
  const next = valueText(type, after);
  const parts = diffWords(was, next);
  return (
    <div className="grid gap-2 md:grid-cols-2" data-testid="diff-view">
      <Side label="Now" empty="(nothing there yet)">
        {was
          ? parts.map((p, i) =>
              p.kind === "same" ? (
                <span key={i}>{p.text}</span>
              ) : p.kind === "del" ? (
                <del key={i} className="rounded-sm bg-destructive/15 text-destructive no-underline decoration-destructive [text-decoration:line-through]">
                  {p.text}
                </del>
              ) : null,
            )
          : null}
      </Side>
      <Side label="Proposed" empty="(waiting for a suggestion)" accent>
        {next
          ? parts.map((p, i) =>
              p.kind === "same" ? (
                <span key={i}>{p.text}</span>
              ) : p.kind === "add" ? (
                <ins key={i} className="rounded-sm bg-success/15 text-success no-underline">
                  {p.text}
                </ins>
              ) : null,
            )
          : null}
      </Side>
    </div>
  );
}

function Side({
  label,
  empty,
  accent,
  children,
}: {
  label: string;
  empty: string;
  accent?: boolean;
  children: React.ReactNode;
}) {
  const hasContent = Array.isArray(children) ? children.some(Boolean) : !!children;
  return (
    <div className={cn("min-w-0 rounded-md border px-3 py-2", accent ? "border-primary/40 bg-primary/5" : "bg-muted/40")}>
      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("text-sm break-words whitespace-pre-wrap", !hasContent && "text-muted-foreground italic")}>
        {hasContent ? children : empty}
      </p>
    </div>
  );
}
