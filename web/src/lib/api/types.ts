export type Role = "owner" | "admin" | "member" | "client_viewer";

export interface User {
  id: string;
  email: string;
  full_name: string | null;
}

export interface Org {
  id: string;
  name: string;
  role: Role;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  /** Only present for desktop clients; the web app relies on the httpOnly cookie. */
  refresh_token: string | null;
  user: User;
  orgs: Org[];
}

export interface MeResponse {
  user: User;
  orgs: Org[];
}

export interface Site {
  id: string;
  org_id: string;
  domain: string;
  uses_www: boolean;
  display_name: string;
  settings: Record<string, unknown>;
  autopilot_enabled: boolean;
  created_at: string;
}

export interface RegisterInput {
  email: string;
  password: string;
  full_name?: string;
  org_name: string;
}
