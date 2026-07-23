"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, clearToken, setToken } from "@/lib/api";
import type { HostRegistration, UserOut } from "@/lib/types";

// Sessionstorage key the dashboard reads on first load after a host
// registration to resume polling the Bright Data scrape triggered during
// signup (see app/api/v1/auth.py register_host -- registration never blocks
// on that scrape, so the poll has to continue somewhere after the redirect).
export const PENDING_IMPORT_KEY = "mira_pending_import";

// Idle-session timeout: log the user out (server-revokes the refresh token
// too, see logout() below) after this long with no activity, so a host who
// walks away from an unlocked device isn't left signed in indefinitely.
// Resets on every tracked interaction rather than counting down from login.
const IDLE_TIMEOUT_MS = 60 * 60 * 1000;
const ACTIVITY_EVENTS = ["mousedown", "keydown", "touchstart", "scroll"] as const;

// Access tokens are short-lived (backend JWT_EXPIRE_MINUTES=20) and live in
// memory only (see lib/api.ts) -- refreshed silently a minute before they'd
// expire, using the HttpOnly refresh-token cookie the browser sends
// automatically. A host who's continuously active never sees a 401 or a
// forced logout; only IDLE_TIMEOUT_MS above actually ends the session early.
const SILENT_REFRESH_MS = 19 * 60 * 1000;

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
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Schedules the next silent POST /auth/refresh call. Reschedules itself
  // on every successful refresh so a continuously-active host is renewed
  // indefinitely (bounded only by the idle timer or the refresh token's own
  // 30-day absolute expiry, not by this loop).
  const scheduleRefresh = useCallback(() => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(async () => {
      try {
        const { access_token } = await api.auth.refresh();
        setToken(access_token);
        scheduleRefresh();
      } catch {
        // Refresh token missing/expired/revoked -- session genuinely over.
        clearToken();
        setUser(null);
      }
    }, SILENT_REFRESH_MS);
  }, []);

  // On mount (including every full page reload, since the access token is
  // memory-only and doesn't survive one -- see lib/api.ts), try to recover
  // the session from the HttpOnly refresh-token cookie rather than requiring
  // a fresh login every time the page is reloaded.
  useEffect(() => {
    (async () => {
      try {
        const { access_token } = await api.auth.refresh();
        setToken(access_token);
        setUser(await api.auth.me());
        scheduleRefresh();
      } catch {
        clearToken();
      } finally {
        setLoading(false);
      }
    })();

    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [scheduleRefresh]);

  const login = useCallback(
    async (email: string, password: string) => {
      const { access_token } = await api.auth.login(email, password);
      setToken(access_token);
      setUser(await api.auth.me());
      scheduleRefresh();
      router.push("/dashboard");
    },
    [router, scheduleRefresh]
  );

  const register = useCallback(
    async (email: string, password: string, name?: string, phone?: string) => {
      const { access_token } = await api.auth.register(email, password, name, phone);
      setToken(access_token);
      setUser(await api.auth.me());
      scheduleRefresh();
      router.push("/dashboard");
    },
    [router, scheduleRefresh]
  );

  const registerHost = useCallback(
    async (data: HostRegistration) => {
      const { access_token, snapshot_id, import_error } = await api.auth.registerHost(data);
      setToken(access_token);
      setUser(await api.auth.me());
      scheduleRefresh();
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
    [router, scheduleRefresh]
  );

  const logout = useCallback(() => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    clearToken();
    setUser(null);
    // Best-effort server-side revoke of the refresh token/cookie -- fire
    // and forget, logout must feel instant client-side regardless of
    // network state.
    api.auth.logout().catch(() => {});
    router.push("/login");
  }, [router]);

  const refreshUser = useCallback(async () => {
    setUser(await api.auth.me());
  }, []);

  // Idle timeout: reset a 60min timer on any user activity while logged in.
  // Clearing local state (rather than router.push) is enough to reflect
  // logged-out UI immediately -- page.tsx / landing-hero.tsx already fall
  // back to the Login/Register view once `user` goes null, no forced
  // navigation needed while the host is just browsing the marketing site.
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!user) return;

    const resetIdleTimer = () => {
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
      idleTimerRef.current = setTimeout(() => {
        if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
        clearToken();
        setUser(null);
        // Server-side revoke so the refresh cookie can't silently resume
        // the session later -- same call logout() makes, but without the
        // /login redirect since an idle host may just be away, not done.
        api.auth.logout().catch(() => {});
      }, IDLE_TIMEOUT_MS);
    };

    resetIdleTimer();
    ACTIVITY_EVENTS.forEach((event) => window.addEventListener(event, resetIdleTimer));

    return () => {
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
      ACTIVITY_EVENTS.forEach((event) => window.removeEventListener(event, resetIdleTimer));
    };
  }, [user]);

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
