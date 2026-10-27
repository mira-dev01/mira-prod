"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { CapabilityStatus, CapabilityUpdate, HostCapabilities } from "@/lib/types";

type CapabilitiesContextValue = {
  data: HostCapabilities | null;
  loading: boolean;
  error: Error | null;
  refetch: () => Promise<void>;
  // Persists through PATCH /capabilities/{id} and applies the response;
  // throws (ApiError) so callers can show the backend's reason.
  update: (id: string, data: CapabilityUpdate) => Promise<HostCapabilities>;
  // Bumped after every successful change, so layout preferences (whose
  // eligibility derives from capability state) know to re-resolve.
  version: number;
};

const CapabilitiesContext = createContext<CapabilitiesContextValue | null>(null);

/**
 * The dashboard's one copy of the host's capability state (Phase 1's
 * GET /capabilities). Settings > Features & modules writes through it and
 * every capability-aware section, the sidebar and Overview read from it,
 * so a change in one place is reflected everywhere without a reload.
 */
export function CapabilitiesProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<HostCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [version, setVersion] = useState(0);
  const latestRef = useRef(0);

  const refetch = useCallback(async () => {
    const requestId = ++latestRef.current;
    try {
      const next = await api.capabilities.list();
      if (requestId === latestRef.current) {
        setData(next);
        setError(null);
      }
    } catch (err) {
      if (requestId === latestRef.current) setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (requestId === latestRef.current) setLoading(false);
    }
  }, []);

  const update = useCallback(async (id: string, change: CapabilityUpdate) => {
    const next = await api.capabilities.update(id, change);
    latestRef.current += 1;
    setData(next);
    setError(null);
    setVersion((v) => v + 1);
    return next;
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- kicking off the fetch is the point of this effect
    void refetch();
  }, [refetch]);

  return (
    <CapabilitiesContext.Provider value={{ data, loading, error, refetch, update, version }}>
      {children}
    </CapabilitiesContext.Provider>
  );
}

export function useCapabilities(): CapabilitiesContextValue {
  const ctx = useContext(CapabilitiesContext);
  if (!ctx) throw new Error("useCapabilities must be used within CapabilitiesProvider");
  return ctx;
}

/** One capability's status, or null while loading/unknown/outside the
 * dashboard -- callers render their normal UI when it's null, so a failed
 * capability fetch never hides anything. */
export function useCapability(id: string): CapabilityStatus | null {
  const ctx = useContext(CapabilitiesContext);
  return ctx?.data?.capabilities.find((c) => c.id === id) ?? null;
}
