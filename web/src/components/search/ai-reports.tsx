import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useRef, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AiReport, AiReportImport, AiReportSource } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { fmtInt, formatDay, shortUrl } from "@/lib/search-utils";

const SOURCES: { source: AiReportSource; title: string; description: string; where: string }[] = [
  {
    source: "gsc_genai",
    title: "Google Search Console: generative AI report",
    description: "How often your pages appeared in Google's AI features.",
    where: "In Search Console, open the report, choose Export, then Download CSV.",
  },
  {
    source: "bing_ai",
    title: "Bing Webmaster Tools: AI Performance",
    description: "Citations, cited pages and grounding queries from Bing's AI answers.",
    where: "In Bing Webmaster Tools, open the report and choose Export to CSV.",
  },
];

function label(metric: string): string {
  return metric.replaceAll("_", " ");
}

function ReportChart({ report }: { report: AiReport }) {
  const metric = report.primary_metric;
  if (!metric || report.series.length === 0) return null;
  const data = report.series.map((p) => ({ date: p.date, value: p.values[metric] ?? 0 }));
  return (
    <div role="img" aria-label={`${label(metric)} per day`} className="h-40 w-full">
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--border, #e5e7eb)" />
          <XAxis dataKey="date" tickFormatter={formatDay} tick={{ fontSize: 10 }} minTickGap={32} />
          <YAxis width={44} tick={{ fontSize: 10 }} tickFormatter={(v: number) => fmtInt(v)} />
          <Tooltip formatter={(v) => [typeof v === "number" ? fmtInt(v) : String(v), label(metric)]} labelFormatter={(l) => formatDay(String(l))} />
          <Line type="monotone" dataKey="value" stroke="var(--chart-2, #7c3aed)" dot={false} strokeWidth={2} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function TopTable({ title, rows, metrics, page }: { title: string; rows: AiReport["top_pages"]; metrics: string[]; page?: boolean }) {
  if (rows.length === 0) return null;
  const cols = metrics.filter((m) => rows.some((r) => m in r.values)).slice(0, 3);
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm" aria-label={title}>
        <thead className="border-b text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">{title}</th>
            {cols.map((m) => (
              <th key={m} className="px-3 py-2 text-right font-medium capitalize">
                {label(m)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((r) => (
            <tr key={r.key}>
              <td className="max-w-[24rem] truncate px-3 py-2" title={r.key}>
                {page ? shortUrl(r.key) : r.key}
              </td>
              {cols.map((m) => (
                <td key={m} className="px-3 py-2 text-right tabular-nums">
                  {m in r.values ? fmtInt(r.values[m] ?? 0) : "–"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type SourceInfo = (typeof SOURCES)[number];

function SourceCard({ siteId, info, canUpload }: { siteId: string; info: SourceInfo; canUpload: boolean }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<AiReportImport | null>(null);
  const report = useQuery({
    queryKey: ["ai-report", activeOrg?.id, siteId, info.source],
    queryFn: () => client.aiReport(siteId, info.source),
  });
  const upload = useMutation({
    mutationFn: (file: File) => client.uploadAiReport(siteId, info.source, file),
    onSuccess: (done) => {
      setResult(done);
      void queryClient.invalidateQueries({ queryKey: ["ai-report", activeOrg?.id, siteId, info.source] });
    },
  });
  const remove = useMutation({
    mutationFn: (importId: string) => client.deleteAiReportImport(siteId, importId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["ai-report", activeOrg?.id, siteId, info.source] }),
  });
  const data = report.data;
  const inputId = `upload-${info.source}`;

  return (
    <Card aria-label={info.title}>
      <CardHeader>
        <CardTitle>{info.title}</CardTitle>
        <CardDescription>{info.description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {canUpload ? (
          <div className="space-y-2">
            <input
              ref={input}
              id={inputId}
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              aria-label={`Upload CSV for ${info.title}`}
              onChange={(e) => {
                const file = e.target.files?.[0];
                setResult(null);
                if (file) upload.mutate(file);
                e.target.value = "";
              }}
            />
            <Button variant="outline" size="sm" disabled={upload.isPending} onClick={() => input.current?.click()}>
              <Upload /> {upload.isPending ? "Importing…" : "Upload CSV"}
            </Button>
            <p className="text-xs text-muted-foreground">{info.where} A newer file replaces overlapping days.</p>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Ask a member to upload the CSV.</p>
        )}

        {upload.isError && (
          <p role="alert" className="whitespace-pre-line rounded-lg border border-destructive/40 p-3 text-sm text-destructive">
            This file could not be imported. {upload.error.message}
          </p>
        )}
        {result && (
          <div role="status" className="space-y-1 rounded-lg border p-3 text-sm">
            <p className="font-medium">
              Imported {result.filename}: {fmtInt(result.row_count)} rows
              {result.skipped_rows ? `, ${fmtInt(result.skipped_rows)} skipped` : ""}.
            </p>
            {(result.warnings ?? []).map((w) => (
              <p key={w} className="text-muted-foreground">
                {w}
              </p>
            ))}
          </div>
        )}

        {report.isPending && <Skeleton className="h-24 w-full" aria-label="Loading report" />}
        {report.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not load this report: {report.error.message}
          </p>
        )}
        {data && data.imports.length === 0 && (
          <p className="text-sm text-muted-foreground">No file uploaded yet. Upload an export to see it here.</p>
        )}
        {data && data.imports.length > 0 && (
          <>
            <ReportChart report={data} />
            <TopTable title="Top pages" rows={data.top_pages} metrics={data.metrics} page />
            <TopTable title="Top queries" rows={data.top_queries} metrics={data.metrics} />
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">Uploaded files ({data.imports.length})</summary>
              <ul className="mt-1 space-y-1">
                {data.imports.map((i) => (
                  <li key={i.id} className="flex items-center justify-between gap-2">
                    <span className="truncate">
                      {i.filename} · {i.shape} · {fmtInt(i.row_count)} rows
                      {i.date_min && i.date_max ? ` · ${i.date_min} to ${i.date_max}` : ""}
                    </span>
                    {canUpload && (
                      <Button variant="ghost" size="sm" disabled={remove.isPending} onClick={() => remove.mutate(i.id)}>
                        Remove
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function AiReportsPanel({ siteId }: { siteId: string }) {
  const { activeOrg } = useAuth();
  const canUpload = activeOrg?.role !== "client_viewer";
  return (
    <section aria-label="AI reports" className="space-y-4">
      <p className="max-w-2xl text-sm text-muted-foreground">
        Google and Bing publish reports on how their AI features use your pages, but only as downloads. Upload the CSV
        here to chart it next to your other data. Nothing is sent anywhere else.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        {SOURCES.map((info) => (
          <SourceCard key={info.source} siteId={siteId} info={info} canUpload={canUpload} />
        ))}
      </div>
    </section>
  );
}
