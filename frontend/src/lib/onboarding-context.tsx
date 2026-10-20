"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { OnboardingState } from "@/lib/types";

type OnboardingContextValue = {
  // null while loading, or if GET /onboarding failed (see `error`).
  state: OnboardingState | null;
  loading: boolean;
  error: Error | null;
  // Applies a state the caller already has (every onboarding mutation
  // returns the full OnboardingState) without a second round-trip.
  setState: (next: OnboardingState) => void;
  refetch: () => Promise<void>;
};

const OnboardingContext = createContext<OnboardingContextValue | null>(null);

/**
 * Server-persisted onboarding state, fetched once per dashboard mount and
 * shared by the layout's onboarding gate, the onboarding wizard and the
 * import banner -- one source of truth, so completing onboarding updates
 * the gate before the redirect to /dashboard rather than bouncing back.
 * Mounted only once a Clerk-backed user exists (see app/dashboard/layout.tsx).
 */
export function OnboardingProvider({ children }: { children: React.ReactNode }) {
  const [state, setStateRaw] = useState<OnboardingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  // Same out-of-order guard as auth-context's latestRequestRef: a slow
  // background refetch must not overwrite a newer mutation's result.
  const latestRef = useRef(0);

  const refetch = useCallback(async () => {
    const requestId = ++latestRef.current;
    try {
      const next = await api.onboarding.get();
      if (requestId === latestRef.current) {
        setStateRaw(next);
        setError(null);
      }
    } catch (err) {
      if (requestId === latestRef.current) setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (requestId === latestRef.current) setLoading(false);
    }
  }, []);

  const setState = useCallback((next: OnboardingState) => {
    latestRef.current += 1;
    setStateRaw(next);
    setError(null);
    setLoading(false);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- kicking off the fetch is the point of this effect
    void refetch();
  }, [refetch]);

  return (
    <OnboardingContext.Provider value={{ state, loading, error, setState, refetch }}>
      {children}
    </OnboardingContext.Provider>
  );
}

export function useOnboarding(): OnboardingContextValue {
  const ctx = useContext(OnboardingContext);
  if (!ctx) throw new Error("useOnboarding must be used within OnboardingProvider");
  return ctx;
}
