import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api as defaultClient, type ApiClient } from "@/lib/api/client";
import type { Org, RegisterInput, TokenResponse, User } from "@/lib/api/types";

const ACTIVE_ORG_KEY = "searchos.activeOrg";

type Session =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: User; orgs: Org[] };

interface AuthContextValue {
  client: ApiClient;
  session: Session;
  activeOrg: Org | null;
  setActiveOrg: (orgId: string) => void;
  login: (email: string, password: string) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => Promise<void>;
  addOrg: (name: string) => Promise<Org>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readActiveOrgId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_ORG_KEY);
  } catch {
    return null;
  }
}

function writeActiveOrgId(id: string | null): void {
  try {
    if (id) localStorage.setItem(ACTIVE_ORG_KEY, id);
    else localStorage.removeItem(ACTIVE_ORG_KEY);
  } catch {
    // ignore
  }
}

export function AuthProvider({ children, client = defaultClient }: { children: ReactNode; client?: ApiClient }) {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<Session>({ status: "loading" });
  const [activeOrgId, setActiveOrgId] = useState<string | null>(readActiveOrgId);

  const applyTokens = useCallback(
    (tokens: Pick<TokenResponse, "user" | "orgs">) => {
      setSession({ status: "authenticated", user: tokens.user, orgs: tokens.orgs });
      setActiveOrgId((current) => {
        const valid = tokens.orgs.some((org) => org.id === current);
        return valid ? current : (tokens.orgs[0]?.id ?? null);
      });
    },
    [],
  );

  // Keep the API client's X-Org-Id in step with the selected org. A layout effect runs before any child's
  // data-fetching effect, so the first query after switching orgs already carries the right header.
  useLayoutEffect(() => {
    client.setOrgId(activeOrgId);
    writeActiveOrgId(activeOrgId);
  }, [client, activeOrgId]);

  const dropSession = useCallback(() => {
    setSession({ status: "anonymous" });
    queryClient.clear();
  }, [queryClient]);

  // Session bootstrap: the httpOnly refresh cookie (if any) yields an access token; otherwise we are logged out.
  const bootstrapped = useRef(false);
  useEffect(() => {
    client.onAuthLost = dropSession;
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    client
      .refresh()
      .then(applyTokens)
      .catch(() => setSession((current) => (current.status === "loading" ? { status: "anonymous" } : current)));
  }, [client, applyTokens, dropSession]);

  const login = useCallback(
    async (email: string, password: string) => {
      applyTokens(await client.login(email, password));
    },
    [client, applyTokens],
  );

  const register = useCallback(
    async (input: RegisterInput) => {
      applyTokens(await client.register(input));
    },
    [client, applyTokens],
  );

  const logout = useCallback(async () => {
    try {
      await client.logout();
    } finally {
      writeActiveOrgId(null);
      setActiveOrgId(null);
      dropSession();
    }
  }, [client, dropSession]);

  const addOrg = useCallback(
    async (name: string) => {
      const org = await client.createOrg(name);
      setSession((current) =>
        current.status === "authenticated" ? { ...current, orgs: [...current.orgs, org] } : current,
      );
      setActiveOrgId(org.id);
      return org;
    },
    [client],
  );

  const setActiveOrg = useCallback((orgId: string) => setActiveOrgId(orgId), []);

  const activeOrg = useMemo(() => {
    if (session.status !== "authenticated") return null;
    return session.orgs.find((org) => org.id === activeOrgId) ?? session.orgs[0] ?? null;
  }, [session, activeOrgId]);

  const value = useMemo<AuthContextValue>(
    () => ({ client, session, activeOrg, setActiveOrg, login, register, logout, addOrg }),
    [client, session, activeOrg, setActiveOrg, login, register, logout, addOrg],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
