import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";

export function SettingsPage() {
  const { session, activeOrg } = useAuth();
  if (session.status !== "authenticated" || !activeOrg) return null;
  return (
    <>
      <PageHeader title="Settings" description="Your account and the active organisation." />
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Account</CardTitle>
            <CardDescription>Who you are signed in as.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p className="font-medium">{session.user.full_name ?? "—"}</p>
            <p className="text-muted-foreground">{session.user.email}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Organisation</CardTitle>
            <CardDescription>The workspace you are currently working in.</CardDescription>
          </CardHeader>
          <CardContent className="flex items-center justify-between gap-2 text-sm">
            <p className="font-medium">{activeOrg.name}</p>
            <Badge>{activeOrg.role.replace("_", " ")}</Badge>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
