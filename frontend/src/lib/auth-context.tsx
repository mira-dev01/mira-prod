"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, clearToken, getToken, setToken } from "@/lib/api";
import type { HostRegistration, UserOut } from "@/lib/types";

// Sessionstorage key the dashboard reads on first load after a host
// registration to resume polling the Bright Data scrape triggered during
// signup (see app/api/v1/auth.py register_host -- registration never blocks
// on that scrape, so the poll has to continue somewhere after the redirect).
export const PENDING_IMPORT_KEY = "mira_pending_import";

type AuthContextValue = {
  user: UserOut | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string, phone?: string) => Promise<void>;
  registerHost: (data: HostRegistration) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null);
  const [loading, setLoading] = useState(() => getToken() !== null);
  const router = useRouter();

  useEffect(() => {
    if (!getToken()) return;
    api.auth
      .me()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const { access_token } = await api.auth.login(email, password);
      setToken(access_token);
      setUser(await api.auth.me());
      router.push("/dashboard");
    },
    [router]
  );

  const register = useCallback(
    async (email: string, password: string, name?: string, phone?: string) => {
      const { access_token } = await api.auth.register(email, password, name, phone);
      setToken(access_token);
      setUser(await api.auth.me());
      router.push("/dashboard");
    },
    [router]
  );

  const registerHost = useCallback(
    async (data: HostRegistration) => {
      const { access_token, snapshot_id, import_error } = await api.auth.registerHost(data);
      setToken(access_token);
      setUser(await api.auth.me());
      if (snapshot_id) {
        window.sessionStorage.setItem(
          PENDING_IMPORT_KEY,
          JSON.stringify({ snapshotId: snapshot_id, icalUrl: data.ical_url ?? null })
        );
      } else if (import_error) {
        window.sessionStorage.setItem(PENDING_IMPORT_KEY, JSON.stringify({ error: import_error }));
      }
      router.push("/dashboard");
    },
    [router]
  );

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    router.push("/login");
  }, [router]);

  const refreshUser = useCallback(async () => {
    setUser(await api.auth.me());
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, registerHost, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
