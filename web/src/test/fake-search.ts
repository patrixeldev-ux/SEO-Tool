import type {
  AiReport,
  AiReportImport,
  GoogleProperties,
  IntegrationStatus,
  Opportunities,
  SearchCoverage,
  SearchPageDetail,
  SearchTable,
  SearchTotals,
  SiteIntegrations,
} from "@/lib/api/types";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export const EMPTY_INTEGRATIONS: SiteIntegrations = { google: null, bing: null, psi: { enabled: true, key_configured: false } };

export function connection(over: Partial<NonNullable<SiteIntegrations["google"]>> = {}): NonNullable<SiteIntegrations["google"]> {
  return {
    id: "c1",
    provider: "google",
    status: "connected",
    account_label: "Google account",
    property_url: "sc-domain:shop.example.com",
    last_sync_at: "2026-10-12T02:00:00Z",
    last_error: null,
    meta: {},
    ...over,
  };
}

export const COVERAGE_EMPTY: SearchCoverage = {
  connected: false,
  property_url: null,
  first_day: null,
  last_day: null,
  days_stored: 0,
  days_expected: 485,
  active_job: null,
  last_job: null,
  last_sync_at: null,
  last_error: null,
};

export function totals(days = 28): SearchTotals {
  const series = Array.from({ length: days }, (_, i) => ({
    date: `2026-09-${String(i + 1).padStart(2, "0")}`,
    clicks: 100 + i,
    impressions: 2000 + i * 10,
    ctr: (100 + i) / (2000 + i * 10),
    position: 6.5,
  }));
  return {
    range: "28d",
    start: series[0]!.date,
    end: series[days - 1]!.date,
    series,
    summary: { clicks: 3500, impressions: 62000, ctr: 3500 / 62000, position: 6.4, days },
    previous: { clicks: 3000, impressions: 60000, ctr: 0.05, position: 6.9, days },
    note: "Totals include queries Google anonymises. The query and page tables below do not, so they add up to less.",
  };
}

export function table(kind: "queries" | "pages", segment = "real"): SearchTable {
  const conv = segment !== "real";
  const rows =
    kind === "queries"
      ? [
          { key: "espresso beans", clicks: 300, impressions: 5000, ctr: 0.06, position: 3.2, is_conversational: false },
          ...(conv ? [{ key: "tell me more", clicks: 1, impressions: 900, ctr: 0.001, position: 6, is_conversational: true }] : []),
        ]
      : [{ key: "https://shop.example.com/beans", clicks: 300, impressions: 5000, ctr: 0.06, position: 3.2, is_conversational: false }];
  return {
    range: "28d",
    segment: segment as SearchTable["segment"],
    start: "2026-09-01",
    end: "2026-09-28",
    total: rows.length,
    rows,
    conversational_impressions: 900,
    all_impressions: 9000,
    note: "Tables leave out queries Google anonymises, so they add up to less than the totals.",
  };
}

export const OPPORTUNITIES: Opportunities = {
  computed: true,
  computed_at: "2026-10-12T02:00:00Z",
  window_start: "2026-09-01",
  window_end: "2026-09-28",
  counts: { STRIKING_DISTANCE: 1, CTR_GAP: 1, DECAY: 1, CANNIBALISATION: 1 },
  skipped: {},
  curve_summary: "site",
  total: 4,
  items: [
    {
      id: "op1",
      type: "STRIKING_DISTANCE",
      title: "Striking distance",
      page: "https://shop.example.com/grinders",
      query: "best coffee grinder",
      score: 412.5,
      explanation: '"best coffee grinder" shows this page on average at position 6.2, 25,000 times in 28 days.',
      evidence: { impressions: 25000, position: 6.2, clicks: 700 },
    },
    {
      id: "op2",
      type: "CTR_GAP",
      title: "Click-through gap",
      page: "https://shop.example.com/french-press",
      query: "french press ratio",
      score: 120,
      explanation: 'For "french press ratio" this page gets 1.0% of clicks at position 3.0.',
      evidence: { impressions: 14000, ctr: 0.01 },
    },
    {
      id: "op3",
      type: "DECAY",
      title: "Losing clicks",
      page: "https://shop.example.com/espresso-basics",
      query: null,
      score: 480,
      explanation: "This page got 320 clicks in the last 28 days against 800 in the 28 days before (-60.0%).",
      evidence: { clicks_last_28d: 320, clicks_previous_28d: 800 },
    },
    {
      id: "op4",
      type: "CANNIBALISATION",
      title: "Pages competing",
      page: "https://shop.example.com/grinders",
      query: "espresso at home",
      score: 300,
      explanation: '"espresso at home" is shared between 2 of your pages.',
      evidence: { query_impressions: 7000 },
    },
  ],
};

export function pageDetail(url: string): SearchPageDetail {
  return {
    page: url,
    range: "28d",
    segment: "real",
    start: "2026-09-01",
    end: "2026-09-28",
    summary: { clicks: 140, impressions: 14000, ctr: 0.01, position: 3.0, days: 28 },
    expected_ctr: 0.1,
    curve_confidence: "site",
    series: [{ date: "2026-09-01", clicks: 5, impressions: 500, position: 3 }],
    queries: [
      { query: "french press ratio", clicks: 140, impressions: 14000, ctr: 0.01, position: 3.0, expected_ctr: 0.1, vs_curve: -0.09, is_conversational: false },
    ],
    opportunities: [OPPORTUNITIES.items[1]!],
    curve: {
      positions: [1, 2, 3, 4, 5],
      values: [0.28, 0.15, 0.1, 0.07, 0.05],
      confidence: ["site", "site", "site", "site", "site"],
      impressions: [1, 1, 1, 1, 1],
      summary: "site",
      computed_at: "2026-10-12T02:00:00Z",
      window_start: "2026-09-01",
      window_end: "2026-09-28",
    },
  };
}

export const STATUS_READY: IntegrationStatus = {
  google: { configured: true, message: "Google is ready to connect." },
  encryption: { configured: true, message: "Credentials are stored encrypted." },
  psi: { enabled: true, key_configured: false },
  redirect_uri: "http://localhost:8000/integrations/google/callback",
  setup_guide: "docs/GOOGLE_SETUP.md",
};

export const STATUS_NOT_CONFIGURED: IntegrationStatus = {
  ...STATUS_READY,
  google: { configured: false, message: "Google isn't set up yet." },
};

export const PROPERTIES: GoogleProperties = {
  properties: [
    { site_url: "sc-domain:shop.example.com", permission_level: "siteOwner", usable: true },
    { site_url: "https://other.example/", permission_level: "siteUnverifiedUser", usable: false },
  ],
  suggested: "sc-domain:shop.example.com",
  selected: null,
};

export function emptyReport(source: AiReport["source"]): AiReport {
  return { source, imports: [], metrics: [], primary_metric: null, series_shape: null, series: [], top_pages: [], top_queries: [] };
}

export interface SearchState {
  status: IntegrationStatus;
  integrations: Map<string, SiteIntegrations>;
  properties: GoogleProperties;
  coverage: Map<string, SearchCoverage>;
  totals: SearchTotals;
  opportunities: Opportunities;
  reports: Map<string, AiReport>;
  /** Detail text the fake API returns with a 422 when a CSV is uploaded; null = accept it. */
  uploadError: string | null;
  uploaded: AiReportImport | null;
  connectUrl: string;
}

export function createSearchState(): SearchState {
  return {
    status: STATUS_READY,
    integrations: new Map(),
    properties: PROPERTIES,
    coverage: new Map(),
    totals: totals(),
    opportunities: OPPORTUNITIES,
    reports: new Map(),
    uploadError: null,
    uploaded: null,
    connectUrl: "https://accounts.google.com/o/oauth2/v2/auth?state=abc",
  };
}

export function handleSearchRoute(
  state: SearchState,
  url: URL,
  method: string,
  body: unknown,
  rawBody: BodyInit | null | undefined,
): Response | null {
  if (url.pathname === "/integrations/status") return json(state.status);
  const m = url.pathname.match(/^\/sites\/([^/]+)\/(integrations|search|ai-reports)(\/.*)?$/);
  if (!m) return null;
  const siteId = m[1]!;
  const area = m[2]!;
  const rest = m[3] ?? "";
  const current = () => state.integrations.get(siteId) ?? EMPTY_INTEGRATIONS;

  if (area === "integrations") {
    if (rest === "" && method === "GET") return json(current());
    if (rest === "/google/connect" && method === "POST") return json({ authorization_url: state.connectUrl });
    if (rest === "/google/properties") return json(state.properties);
    if (rest === "/google/property" && method === "PUT") {
      const siteUrl = (body as { site_url: string }).site_url;
      const next = { ...current(), google: connection({ property_url: siteUrl }) };
      state.integrations.set(siteId, next);
      return json(next.google);
    }
    if (rest === "/google/sync") return json({ id: "j1", type: "gsc_sync", status: "queued", progress: 0, error: null, payload: {}, result: null }, 202);
    if (rest === "/google" && method === "DELETE") {
      state.integrations.set(siteId, { ...current(), google: null });
      return new Response(null, { status: 204 });
    }
    if (rest === "/bing" && method === "PUT") {
      const key = (body as { api_key: string }).api_key;
      if (key === "bad-key-123456") return json({ detail: "Bing answered 400: InvalidApiKey" }, 502);
      const next = { ...current(), bing: connection({ id: "b1", provider: "bing", property_url: "https://shop.example.com/", account_label: "Bing Webmaster API key" }) };
      state.integrations.set(siteId, next);
      return json(next.bing);
    }
    if (rest === "/bing" && method === "DELETE") {
      state.integrations.set(siteId, { ...current(), bing: null });
      return new Response(null, { status: 204 });
    }
    return null;
  }

  if (area === "search") {
    if (rest === "/coverage") return json(state.coverage.get(siteId) ?? COVERAGE_EMPTY);
    if (rest === "/totals") return json(state.totals);
    if (rest === "/queries" || rest === "/pages") {
      return json(table(rest === "/queries" ? "queries" : "pages", url.searchParams.get("segment") ?? "real"));
    }
    if (rest === "/opportunities") {
      const type = url.searchParams.get("type");
      const items = type ? state.opportunities.items.filter((i) => i.type === type) : state.opportunities.items;
      return json({ ...state.opportunities, items, total: items.length });
    }
    if (rest === "/page") return json(pageDetail(url.searchParams.get("url") ?? ""));
    return null;
  }

  // ai-reports
  const source = rest.match(/^\/(gsc_genai|bing_ai)(\/import)?$/);
  if (source && source[2] && method === "POST") {
    if (state.uploadError) return json({ detail: state.uploadError }, 422);
    const done: AiReportImport = state.uploaded ?? {
      id: "imp1",
      source: source[1]!,
      filename: (rawBody as FormData | undefined)?.get?.("file") instanceof File ? ((rawBody as FormData).get("file") as File).name : "upload.csv",
      shape: "date",
      row_count: 14,
      date_min: "2026-09-01",
      date_max: "2026-09-14",
      columns: ["Date", "Impressions"],
      created_at: "2026-10-12T10:00:00Z",
      metrics: ["impressions"],
      skipped_rows: 0,
      warnings: [],
    };
    state.reports.set(`${siteId}:${source[1]}`, {
      source: source[1] as AiReport["source"],
      imports: [done],
      metrics: ["impressions"],
      primary_metric: "impressions",
      series_shape: "date",
      series: [
        { date: "2026-09-01", values: { impressions: 10 } },
        { date: "2026-09-02", values: { impressions: 14 } },
      ],
      top_pages: [{ key: "https://shop.example.com/beans", values: { impressions: 24 } }],
      top_queries: [],
    });
    return json(done, 201);
  }
  if (source && method === "GET") {
    return json(state.reports.get(`${siteId}:${source[1]}`) ?? emptyReport(source[1] as AiReport["source"]));
  }
  return null;
}
