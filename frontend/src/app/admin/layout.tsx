"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { DateRangeProvider } from "@/components/date-range-context";
import { DateRangePicker } from "@/components/date-range-picker";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { AdminProvider, useAdmin } from "@/components/admin/admin-context";
import { AdminSidebar } from "@/components/admin/admin-sidebar";
import { adminApi, getAdminSession, setAdminSession } from "@/lib/admin-api";

function FilterBar() {
  const { includeTestCalls, setIncludeTestCalls } = useAdmin();
  return (
    <div className="mb-5 flex flex-wrap items-center justify-end gap-4">
      <div className="flex items-center gap-2">
        <Switch id="admin-include-test" checked={includeTestCalls} onCheckedChange={setIncludeTestCalls} />
        <Label htmlFor="admin-include-test" className="text-sm text-muted-foreground">
          Include browser test calls
        </Label>
      </div>
      <DateRangePicker />
    </div>
  );
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname === "/admin/login";
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    if (isLogin) return;
    const session = getAdminSession();
    if (!session) {
      router.replace("/admin/login");
      return;
    }
    // Confirms the token server-side (also catches an address removed from
    // the allowlist since the token was issued).
    adminApi.auth
      .me()
      .then((me) => setEmail(me.email))
      .catch(() => {
        setAdminSession(null);
        router.replace("/admin/login");
      });
  }, [isLogin, router]);

  const logout = useCallback(() => {
    setAdminSession(null);
    router.replace("/admin/login");
  }, [router]);

  if (isLogin) return <>{children}</>;
  if (!email) {
    return <div className="fixed inset-0 flex items-center justify-center text-sm text-muted-foreground">Loading…</div>;
  }

  return (
    <AdminProvider email={email} logout={logout}>
      <DateRangeProvider defaultDays={7}>
        <div className="fixed inset-0 flex overflow-hidden">
          <AdminSidebar />
          <main className="min-h-0 flex-1 overflow-y-auto p-4 pt-[calc(3.5rem+1rem)] md:p-6 md:pt-6">
            {/* Balances are point-in-time, not range-scoped -- no filters there. */}
            {!pathname.startsWith("/admin/balances") && <FilterBar />}
            <div className="space-y-5">{children}</div>
          </main>
        </div>
      </DateRangeProvider>
    </AdminProvider>
  );
}
