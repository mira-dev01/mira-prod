"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { OnboardingProvider, useOnboarding } from "@/lib/onboarding-context";
import { CapabilitiesProvider } from "@/lib/capabilities-context";
import { NavigationProvider } from "@/lib/navigation-context";
import { SidebarNav } from "@/components/sidebar-nav";
import { DateRangeProvider } from "@/components/date-range-context";
import { PendingImportBanner } from "@/components/pending-import-banner";

function FullScreenLoading() {
  return <div className="fixed inset-0 flex items-center justify-center text-sm text-muted-foreground">Loading…</div>;
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) return <FullScreenLoading />;

  return (
    <OnboardingProvider>
      <OnboardingGate>{children}</OnboardingGate>
    </OnboardingProvider>
  );
}

// Hosts who haven't finished onboarding are sent to it (server-persisted --
// see backend app/services/onboarding_service.py; every pre-existing host
// was marked onboarded by migration, so this never re-onboards them). Waits
// for the onboarding state before rendering anything, so the dashboard
// never flashes for a host who's about to be redirected. If the state
// can't be loaded at all, fails open to the normal dashboard rather than
// locking an existing host out.
function OnboardingGate({ children }: { children: React.ReactNode }) {
  const { state, loading } = useOnboarding();
  const pathname = usePathname();
  const router = useRouter();
  const onOnboardingRoute = pathname.startsWith("/dashboard/onboarding");
  const needsOnboarding = state !== null && state.status !== "completed";

  useEffect(() => {
    if (needsOnboarding && !onOnboardingRoute) router.replace("/dashboard/onboarding");
  }, [needsOnboarding, onOnboardingRoute, router]);

  if (loading && state === null) return <FullScreenLoading />;

  // The wizard is a focused, full-screen flow -- no sidebar to wander off
  // into pages that would just redirect back here.
  if (onOnboardingRoute) {
    return <div className="fixed inset-0 overflow-y-auto bg-background">{children}</div>;
  }

  if (needsOnboarding) return <FullScreenLoading />;

  // Capability state and the user's layout preferences are loaded once here
  // and shared by the sidebar, Overview and Settings (one source of truth).
  return (
    <CapabilitiesProvider>
    <NavigationProvider>
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
    </NavigationProvider>
    </CapabilitiesProvider>
  );
}
