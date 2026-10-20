"use client";

import { createContext, useContext, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { AdminApiError, type AdminFilters } from "@/lib/admin-api";

type AdminContextValue = {
  email: string;
  includeTestCalls: boolean;
  setIncludeTestCalls: (v: boolean) => void;
  logout: () => void;
};

export const AdminContext = createContext<AdminContextValue | null>(null);

export function useAdmin() {
  const ctx = useContext(AdminContext);
  if (!ctx) throw new Error("useAdmin must be used within the admin layout");
  return ctx;
}

export function AdminProvider({
  email,
  logout,
  children,
}: {
  email: string;
  logout: () => void;
  children: React.ReactNode;
}) {
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const value = useMemo(
    () => ({ email, includeTestCalls, setIncludeTestCalls, logout }),
    [email, includeTestCalls, logout]
  );
  return <AdminContext.Provider value={value}>{children}</AdminContext.Provider>;
}

/** The header's date range + test-call toggle as API filters. */
export function useAdminFilters(): AdminFilters {
  const { startDateISO, endDateISO } = useDateRange();
  const { includeTestCalls } = useAdmin();
  return useMemo(
    () => ({ startDate: startDateISO, endDate: endDateISO, includeTestCalls }),
    [startDateISO, endDateISO, includeTestCalls]
  );
}

/** useAsync keyed on the admin filters; an expired/revoked session sends the
 * admin back to the login page instead of showing a broken section. */
export function useAdminQuery<T>(fetcher: (f: AdminFilters) => Promise<T>) {
  const filters = useAdminFilters();
  const router = useRouter();
  const result = useAsync(
    () =>
      fetcher(filters).catch((err) => {
        if (err instanceof AdminApiError && (err.status === 401 || err.status === 403)) router.replace("/admin/login");
        throw err;
      }),
    [filters.startDate, filters.endDate, filters.includeTestCalls]
  );
  return { ...result, filters };
}
