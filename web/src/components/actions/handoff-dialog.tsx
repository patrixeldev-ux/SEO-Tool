import { useQuery } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

export function HandoffDialog({ siteId, open, onOpenChange }: { siteId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const { client, activeOrg } = useAuth();
  const [includeProposed, setIncludeProposed] = useState(false);
  const [copied, setCopied] = useState<"yes" | "failed" | null>(null);
  const query = useQuery({
    queryKey: ["handoff", activeOrg?.id, siteId, includeProposed],
    queryFn: () => client.handoff(siteId, includeProposed),
    enabled: open,
  });

  async function copy() {
    try {
      await navigator.clipboard.writeText(query.data?.text ?? "");
      setCopied("yes");
    } catch {
      setCopied("failed");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Developer handoff</DialogTitle>
          <DialogDescription>
            Plain text to paste into a ticket or an email: every approved change with the page, what it is now and what to put
            there, plus technical tasks.
          </DialogDescription>
        </DialogHeader>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={includeProposed}
            onChange={(e) => {
              setIncludeProposed(e.target.checked);
              setCopied(null);
            }}
            className="size-4 accent-primary"
          />
          Include suggestions I have not approved yet
        </label>
        {query.isPending && <Skeleton className="h-48 w-full" aria-label="Preparing the handoff" />}
        {query.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not prepare the handoff: {query.error.message}
          </p>
        )}
        {query.data && (
          <>
            <pre
              aria-label="Developer handoff text"
              className="max-h-[45vh] overflow-auto rounded-md border bg-muted/40 p-3 text-xs whitespace-pre-wrap"
            >
              {query.data.text}
            </pre>
            <div className="flex items-center gap-3">
              <Button onClick={copy}>
                {copied === "yes" ? <Check /> : <Copy />}
                {copied === "yes" ? "Copied" : "Copy to clipboard"}
              </Button>
              <span className="text-xs text-muted-foreground">{query.data.count} change(s)</span>
              {copied === "failed" && (
                <span role="alert" className="text-xs text-destructive">
                  Your browser blocked copying. Select the text and copy it by hand.
                </span>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
