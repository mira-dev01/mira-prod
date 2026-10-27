"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useCapabilities } from "@/lib/capabilities-context";
import type { NavigationPreferences, NavigationPreferencesUpdate } from "@/lib/types";

type NavigationContextValue = {
  // null while loading or if the request failed -- the sidebar then falls
  // back to the default destinations (lib/navigation.ts FALLBACK_NAV).
  prefs: NavigationPreferences | null;
  loading: boolean;
  error: Error | null;
  refetch: () => Promise<void>;
  // Throw (ApiError) on failure so the editor keeps the host's draft.
  save: (data: NavigationPreferencesUpdate) => Promise<NavigationPreferences>;
  reset: () => Promise<NavigationPreferences>;
  editing: boolean;
  setEditing: (editing: boolean) => void;
};

const NavigationContext = createContext<NavigationContextValue | null>(null);

/**
 * The signed-in user's resolved sidebar (GET /preferences/navigation): order,
 * hidden entries and eligibility, already reconciled server-side against
 * the current capability state. Re-resolves whenever a capability changes,
 * so turning a feature off or on updates the sidebar immediately.
 */
export function NavigationProvider({ children }: { children: React.ReactNode }) {
  const { version: capabilitiesVersion } = useCapabilities();
  const [prefs, setPrefs] = useState<NavigationPreferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [editing, setEditing] = useState(false);
  const latestRef = useRef(0);

  const apply = useCallback((next: NavigationPreferences) => {
    latestRef.current += 1;
    setPrefs(next);
    setError(null);
    setLoading(false);
    return next;
  }, []);

  const refetch = useCallback(async () => {
    const requestId = ++latestRef.current;
    try {
      const next = await api.preferences.navigation();
      if (requestId === latestRef.current) {
        setPrefs(next);
        setError(null);
      }
    } catch (err) {
      if (requestId === latestRef.current) setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (requestId === latestRef.current) setLoading(false);
    }
  }, []);

  const save = useCallback(
    async (data: NavigationPreferencesUpdate) => apply(await api.preferences.saveNavigation(data)),
    [apply]
  );
  const reset = useCallback(async () => apply(await api.preferences.resetNavigation()), [apply]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- kicking off the fetch is the point of this effect
    void refetch();
  }, [refetch, capabilitiesVersion]);

  return (
    <NavigationContext.Provider value={{ prefs, loading, error, refetch, save, reset, editing, setEditing }}>
      {children}
    </NavigationContext.Provider>
  );
}

export function useNavigation(): NavigationContextValue {
  const ctx = useContext(NavigationContext);
  if (!ctx) throw new Error("useNavigation must be used within NavigationProvider");
  return ctx;
}
