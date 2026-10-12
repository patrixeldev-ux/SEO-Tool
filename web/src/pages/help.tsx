import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";

import { MarkdownLite } from "@/components/markdown-lite";
import { Button } from "@/components/ui/button";
import guide from "@/content/google-setup.md?raw";

/** The in-app copy of docs/GOOGLE_SETUP.md (a test keeps the two identical). */
export function GoogleSetupPage() {
  return (
    <div className="max-w-3xl">
      <Button asChild variant="ghost" size="sm" className="mb-4">
        <Link to="/settings">
          <ArrowLeft /> Back to Settings
        </Link>
      </Button>
      <MarkdownLite source={guide} />
    </div>
  );
}
