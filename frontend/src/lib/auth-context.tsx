"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth as useClerkAuth, useClerk, useOrganization, useOrganizationList } from "@clerk/nextjs";
import { api, setTokenGetter } from "@/lib/api";
import type { UserOut } from "@/lib/types";

// Sessionstorage key the dashboard reads on first load after onboarding to
// resume polling the Bright Data scrape triggered by POST /auth/onboarding
// (that call never blocks on the scrape, so the poll has to continue
// somewhere after the redirect).
export const PENDING_IMPORT_KEY = "mira_pending_import";

type AuthContextValue = {
  user: UserOut | null;
  loading: boolean;
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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { isLoaded, isSignedIn, getToken } = useClerkAuth();
  const { signOut } = useClerk();
  const { organization } = useOrganization();
  const { isLoaded: orgListLoaded, setActive, userMemberships } = useOrganizationList({
    userMemberships: true,
  });
  const [user, setUser] = useState<UserOut | null>(null);
  const [profileLoading, setProfileLoading] = useState(true);
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
      setProfileLoading(false);
      return;
    }
    const requestId = ++latestRequestRef.current;
    setProfileLoading(true);
    api.auth
      .me()
      .then((fetched) => applyLatestUser(requestId, fetched))
      .catch(() => applyLatestUser(requestId, null))
      .finally(() => setProfileLoading(false));
    // organization?.id is a dependency, not just isLoaded/isSignedIn --
    // setActive() above swaps the session token (it now carries an org_id
    // claim), so /auth/me has to be re-fetched for is_internal_org to
    // reflect the newly-active org instead of the stale pre-activation one.
  }, [isLoaded, isSignedIn, organization?.id]);

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
