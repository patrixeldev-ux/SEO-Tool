import { Hourglass } from "lucide-react";

import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";

export function ComingSoonPage({ title, description, phase }: { title: string; description: string; phase: string }) {
  return (
    <>
      <PageHeader title={title} description={description} />
      <Card className="items-center py-14 text-center">
        <CardContent className="flex max-w-sm flex-col items-center gap-3">
          <span className="flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <Hourglass className="size-5" />
          </span>
          <h2 className="text-lg font-semibold">Not available yet</h2>
          <p className="text-sm text-muted-foreground">This section is planned for {phase}.</p>
        </CardContent>
      </Card>
    </>
  );
}
