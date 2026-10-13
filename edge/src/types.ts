export interface Env {
  /** Workers KV namespace that caches the signed rules (binding name RULES). */
  RULES: KVNamespace;
  /** Wrangler secret: the per-site signing secret shown in SearchOS (Settings > Deploy). */
  SIGNING_SECRET: string;
  /** Plain variable: the site token shown in SearchOS. */
  SITE_TOKEN: string;
  /** Plain variable: where rules are fetched, e.g. https://api.example.com */
  API_BASE: string;
  /** Plain variable: set to "1" to switch the Worker off completely (pure passthrough). */
  SEARCHOS_DISABLED?: string;
}

export type Op =
  | { op: "title"; value: string }
  | { op: "meta_description"; value: string }
  | { op: "canonical"; value: string }
  | { op: "alt"; src: string; value: string }
  | {
      op: "jsonld";
      id: string;
      value: Record<string, unknown>;
      replace: null | "invalid" | { types: string[] };
    }
  | { op: "link"; id: string; anchor: string; to: string };

export interface Rules {
  v: number;
  issued_at: number;
  enabled: boolean;
  host: string;
  pages: Record<string, Op[]>;
}
