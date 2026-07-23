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
    return (
      <div className="fixed inset-0 flex items-center justify-center text-sm text-muted-foreground">Loading…</div>
    );
  }

  return (
    <DateRangeProvider>
      {/* fixed inset-0 (not h-screen + flex-1) -- this div is a flex item of
          body (app/layout.tsx's `body` is `flex flex-col min-h-full`, not a
          fixed height, so it sizes to fit its content). Combined with
          flex-1's flex-basis:0%, that let a tall dashboard page (e.g.
          Properties with many cards) override this element's own h-screen,
          stretch it -- and body -- to the page's full content height, and
          scroll the DOCUMENT instead of just `main` below. That dragged the
          sidebar along with it despite its own `sticky` (sticky only has
          room to work inside a scroll container shorter than its content).
          fixed inset-0 takes this completely out of that flow -- it's
          always exactly the viewport, independent of body/content height. */}
      <div className="fixed inset-0 flex overflow-hidden">
        <SidebarNav />
        <main className="min-h-0 flex-1 overflow-y-auto p-4 pt-[calc(3.5rem+1rem)] md:p-6 md:pt-6">
          <PendingImportBanner />
          {children}
        </main>
      </div>
    </DateRangeProvider>
  );
}
