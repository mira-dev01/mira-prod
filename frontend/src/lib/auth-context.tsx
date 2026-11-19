"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth as useClerkAuth, useClerk, useOrganization, useOrganizationList } from "@clerk/nextjs";
import { ApiError, api, setTokenGetter } from "@/lib/api";
import type { UserOut } from "@/lib/types";

/** Why a signed-in person has no usable profile, so the dashboard can say so instead of bouncing them to
 * the sign-in page (which sends a signed-in person straight back: the "login loop"). */
export type ProfileProblem = {
  // unavailable: couldn't reach the server (retry). unauthorized: the server didn't accept the session (retry,
  // then sign out). conflict / forbidden: the account itself is the problem (retrying won't help).
  kind: "unavailable" | "unauthorized" | "conflict" | "forbidden";
  message: string;
};

type AuthContextValue = {
  user: UserOut | null;
  loading: boolean;
  /** Clerk says there is a session. Only when this is false does the dashboard send you to sign in. */
  isSignedIn: boolean;
  /** Set when signed in but the profile could not be loaded (and `user` is null). */
  profileProblem: ProfileProblem | null;
  retryProfile: () => void;
  isInternalOrg: boolean;
  logout: () => void;
  refreshUser: () => Promise<void>;
  // Applies a UserOut a caller already has in hand (e.g. a PATCH /auth/me
  // response) directly, without a second round-trip. Still routed through
  // the same sequence guard as refreshUser/the mount-effect fetch below, so
  // it can't be clobbered by an older in-flight /auth/me response landing
  // right after it.
  setUserData: (user: UserOut) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

// A server or network hiccup is retried quietly before the host sees anything: 0.8s, 2s, 4s.
const PROFILE_RETRY_DELAYS_MS = [800, 2000, 4000];

function isTransient(err: unknown): boolean {
  if (!(err instanceof ApiError)) return true; // fetch itself failed: offline, DNS, CORS on a dead gateway
  return err.status === 408 || err.status === 429 || err.status >= 500;
}

function describeProblem(err: unknown): ProfileProblem {
  if (err instanceof ApiError) {
    if (err.status === 409) return { kind: "conflict", message: err.message };
    if (err.status === 403) return { kind: "forbidden", message: err.message };
    if (err.status === 401) {
      return { kind: "unauthorized", message: "We couldn't verify your sign-in. Try again, or sign out and back in." };
    }
  }
  return { kind: "unavailable", message: "We couldn't load your account. Check your connection and try again." };
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const clerkAuth = useClerkAuth();
  // Local design preview only (NEXT_PUBLIC_UI_PREVIEW=1): behave as signed in.
  const preview = process.env.NODE_ENV !== "production" && process.env.NEXT_PUBLIC_UI_PREVIEW === "1";
  const isLoaded = preview || clerkAuth.isLoaded;
  // The public landing page ("/") is previewed signed out.
  const previewSignedOut = preview && typeof window !== "undefined" && window.location.pathname === "/";
  const isSignedIn = (preview && !previewSignedOut) || (!preview && (clerkAuth.isSignedIn ?? false));
  const getToken = clerkAuth.getToken;
  const { signOut } = useClerk();
  const { organization } = useOrganization();
  const { isLoaded: orgListLoaded, setActive, userMemberships } = useOrganizationList({
    userMemberships: true,
  });
  const [user, setUser] = useState<UserOut | null>(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileProblem, setProfileProblem] = useState<ProfileProblem | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const router = useRouter();
  const autoActivatedRef = useRef(false);
  // Guards against out-of-order /auth/me responses: the mount/org-change
  // effect below and refreshUser() can both be in flight at once (e.g. a
  // Settings save's refreshUser() racing the effect's own re-fetch from an
  // org-context change). Without this, an older request's response can
  // resolve AFTER a newer one and silently overwrite fresher data with
  // stale data -- every write to `user` bumps and stamps this ref first, and
  // a response only applies if it's still carrying the latest stamp.
  const latestRequestRef = useRef(0);

  function applyLatestUser(requestId: number, next: UserOut | null) {
    if (requestId === latestRequestRef.current) setUser(next);
  }

  useEffect(() => {
    setTokenGetter(isSignedIn ? getToken : null);
  }, [isSignedIn, getToken]);

  // Being a member of an org isn't the same as it being *active* in the
  // session -- the org_id claim the backend checks (see get_current_user's
  // is_internal_org) only appears on the token once an org is active, and
  // Clerk never does that automatically. Prefer the Mira Dev org if the user
  // is a member of it (internal team members may also belong to a personal
  // host org from earlier testing) -- otherwise fall back to whichever
  // membership they have, matching this app's 1-active-org-per-account model.
  useEffect(() => {
    if (!isSignedIn || !orgListLoaded || organization || autoActivatedRef.current) return;
    const memberships = userMemberships.data;
    if (memberships && memberships.length > 0) {
      const devOrgId = process.env.NEXT_PUBLIC_CLERK_DEV_ORG_ID;
      const target = memberships.find((m) => m.organization.id === devOrgId) ?? memberships[0];
      autoActivatedRef.current = true;
      setActive?.({ organization: target.organization.id });
    }
  }, [isSignedIn, orgListLoaded, organization, userMemberships.data, setActive]);

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      latestRequestRef.current += 1;
      setUser(null);
      setProfileProblem(null);
      setProfileLoading(false);
      return;
    }
    const requestId = ++latestRequestRef.current;
    let cancelled = false; // this effect run was replaced (deps changed) or the provider unmounted
    setProfileLoading(true);
    setProfileProblem(null);
    (async () => {
      // Only a failure of the *account* (or of the server) lands here as a problem the host is shown. It is
      // never turned into "signed out": that is Clerk's call (isSignedIn), and acting on anything else sent a
      // signed-in host to the sign-in page, which sent them straight back.
      for (let attempt = 0; ; attempt++) {
        try {
          const fetched = await api.auth.me();
          applyLatestUser(requestId, fetched);
          return;
        } catch (err) {
          // One retry for a 401 too: the first token after a tab wakes can be stale.
          const retryable = isTransient(err) || (err instanceof ApiError && err.status === 401 && attempt === 0);
          if (retryable && attempt < PROFILE_RETRY_DELAYS_MS.length) {
            await sleep(PROFILE_RETRY_DELAYS_MS[attempt]);
            if (cancelled || requestId !== latestRequestRef.current) return; // superseded while waiting
            continue;
          }
          if (requestId === latestRequestRef.current) {
            setUser(null);
            setProfileProblem(describeProblem(err));
          }
          return;
        }
      }
    })().finally(() => {
      if (!cancelled) setProfileLoading(false);
    });
    // organization?.id is a dependency, not just isLoaded/isSignedIn --
    // setActive() above swaps the session token (it now carries an org_id
    // claim), so /auth/me has to be re-fetched for is_internal_org to
    // reflect the newly-active org instead of the stale pre-activation one.
    // retryNonce is the "Try again" button on the account-problem screen.
    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, organization?.id, retryNonce]);

  const retryProfile = useCallback(() => setRetryNonce((n) => n + 1), []);

  const logout = useCallback(() => {
    signOut(() => router.push("/login"));
  }, [signOut, router]);

  const refreshUser = useCallback(async () => {
    const requestId = ++latestRequestRef.current;
    const fetched = await api.auth.me();
    applyLatestUser(requestId, fetched);
  }, []);

  const setUserData = useCallback((next: UserOut) => {
    // A caller already has a fresh UserOut in hand (e.g. a PATCH response)
    // -- still bump the sequence so it wins over any older fetch still in
    // flight, without making a redundant GET /auth/me round-trip.
    latestRequestRef.current += 1;
    setUser(next);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading: !isLoaded || (isSignedIn && profileLoading),
        isSignedIn,
        profileProblem,
        retryProfile,
        isInternalOrg: user?.is_internal_org ?? false,
        logout,
        refreshUser,
        setUserData,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
