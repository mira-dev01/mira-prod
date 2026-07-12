"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { SidebarNav } from "@/components/sidebar-nav";
import { DateRangeProvider } from "@/components/date-range-context";
import { PendingImportBanner } from "@/components/pending-import-banner";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) {
    return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">Loading…</div>;
  }

  return (
    <DateRangeProvider>
      <div className="flex h-screen flex-1 overflow-hidden">
        <SidebarNav />
        {/* pt-14 on mobile offsets the fixed top bar; md:pt-0 removes it on desktop */}
        <main className="flex-1 overflow-y-auto p-4 pt-[calc(3.5rem+1rem)] md:p-6 md:pt-6">
          <PendingImportBanner />
          {children}
        </main>
      </div>
    </DateRangeProvider>
  );
}
