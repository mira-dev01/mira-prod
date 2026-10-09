"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { LayoutGrid } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { LeadDetailPanel } from "@/components/lead-detail-panel";
import { DateRangePicker } from "@/components/date-range-picker";
import {
  OverviewCustomizer,
  draftFromLayout,
  type OverviewDraft,
} from "@/components/overview/overview-customizer";
import { FALLBACK_WIDGETS, OverviewWidgetGrid, type OverviewWidgetData } from "@/components/overview/widgets";
import { cn, glassCardClassName } from "@/lib/utils";
import type { LeadOut, OverviewWidget } from "@/lib/types";

export default function OverviewPage() {
  // useSearchParams (?customize=1 from Settings) needs a Suspense boundary.
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <OverviewPageContent />
    </Suspense>
  );
}

function OverviewPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const { startDateISO, endDateISO } = useDateRange();

  // One request for the snapshot + attention items. Reporting-period
  // numbers follow the date picker; attention items are current-state (see
  // backend analytics_service.overview).
  const {
    data: overview,
    loading: overviewLoading,
    error: overviewError,
    refetch: refetchOverview,
  } = useAsync(
    () => api.analytics.overview({ startDate: startDateISO, endDate: endDateISO, includeTestCalls }),
    [startDateISO, endDateISO, includeTestCalls]
  );
  const { data: calls, loading: callsLoading } = useAsync(
    () => api.calls.list({ startDate: startDateISO, endDate: endDateISO, limit: 5, includeTestCalls }),
    [startDateISO, endDateISO, includeTestCalls]
  );
  // Unfiltered by date range -- Live requests is "what's open right now,"
  // not a report scoped to the header's date picker (matches the old
  // NotificationsFeed's behavior, which also ignored the date range).
  const { data: leads, loading: leadsLoading, refetch: refetchLeads } = useAsync(() => api.leads.list({}), []);
  const [editingLead, setEditingLead] = useState<LeadOut | null>(null);

  // Saved widget layout (GET /preferences/overview-widgets), already
  // reconciled server-side against the host's capabilities. If it can't
  // load, the recommended layout renders -- the Overview never goes blank.
  const {
    data: layout,
    loading: layoutLoading,
    error: layoutError,
    refetch: refetchLayout,
    setData: setLayout,
  } = useAsync(() => api.preferences.overviewWidgets(), []);
  const [editing, setEditing] = useState(() => searchParams.get("customize") === "1");
  const [draft, setDraft] = useState<OverviewDraft | null>(null);
  const activeDraft = editing && layout ? (draft ?? draftFromLayout(layout)) : null;

  function closeEditor() {
    setEditing(false);
    setDraft(null);
    if (searchParams.get("customize")) router.replace("/dashboard", { scroll: false });
  }

  const widgets: OverviewWidget[] | null = activeDraft
    ? activeDraft.shown
    : layout
      ? layout.widgets.filter((w) => w.available && !w.hidden)
      : layoutError
        ? FALLBACK_WIDGETS.filter((w) => !w.hidden)
        : null;

  const widgetData: OverviewWidgetData = {
    overview,
    overviewLoading,
    overviewError,
    refetchOverview,
    calls,
    callsLoading,
    leads,
    leadsLoading,
    refetchLeads,
    onLeadClick: setEditingLead,
  };

  return (
    // One unified glass panel, not a decorative background layer plus
    // separately-padded content: this outer div IS the frosted surface
    // (glassCardClassName's bg/ring/blur/shadow + the gradient mesh as its
    // own background-image), bled flush to <main>'s edges via negative
    // margin (-m-6 at md+ cancels main's own md:p-6 on all four sides --
    // sidebar included, since this is a sibling of <SidebarNav> in
    // dashboard/layout.tsx, not an overlap risk), then given its own p-6
    // back so the header/cards inside sit inset with real breathing room
    // instead of touching the panel's edges. Mobile only bleeds
    // left/right/bottom (-mx-4 -mb-4); top keeps main's
    // pt-[calc(3.5rem+1rem)] untouched since that's reserved space for the
    // fixed mobile header bar, not decorative padding to cancel. No
    // overflow-hidden needed for the rounded corners -- border-radius
    // clips an element's own background paint by default, so popovers
    // (DateRangePicker) and the LeadDetailPanel drawer, both portaled,
    // aren't at risk of being clipped. Percentage-based blob positions
    // keep the gradient proportionally distributed top-to-bottom on any
    // page length. Every card below opts into the same glassCardClassName
    // (lib/utils.ts) so it reads as its own frosted surface layered on top
    // of this one, matching the reference's layered-glass look. Same
    // radial-gradient + color-mix(in oklch, var(--token)) technique
    // already used in components/hero/call-flow-showcase.tsx.
    <div
      className={cn(
        "-mx-4 -mb-4 space-y-5 rounded-3xl p-4 md:-m-6 md:p-6",
        glassCardClassName
      )}
      style={{
        // Same three palette tokens as always (accent-warm/primary/
        // chart-2), darkened by mixing 55/45 with --foreground before
        // blending toward transparent for the blob's softness -- but two
        // things from the previous version are gone, both confirmed live
        // (isolated test page, bypassing the auth-gated app entirely) to
        // be the actual source of a grey/lavender cast, not the oklch/srgb
        // interpolation space:
        //  1. The wide, faint standalone --foreground wash across the
        //     whole panel -- a neutral tint layered under three already-
        //     desaturating blobs just compounded the greyness. Removed;
        //     the blobs alone now carry all the depth.
        //  2. Low per-blob opacity (12-20%) diluted the already-darkened
        //     color right back toward the (light, neutral-ish) card
        //     background, which is what actually produced the grey --
        //     not the mix space. Raised to 20-30% so the blob's own warm
        //     hue stays visible instead of washing out.
        // `in srgb` kept (not `in oklch`) since it's still the more
        // predictable space for a plain "blend toward ink" step; see
        // glassCardClassName in lib/utils.ts for the base-tint half of
        // this same fix.
        backgroundImage:
          "radial-gradient(55% 45% at 12% 10%, color-mix(in srgb, color-mix(in srgb, var(--accent-warm) 55%, var(--foreground) 45%) 30%, transparent), transparent 72%), radial-gradient(50% 40% at 88% 6%, color-mix(in srgb, color-mix(in srgb, var(--primary) 55%, var(--foreground) 45%) 26%, transparent), transparent 72%), radial-gradient(45% 35% at 50% 55%, color-mix(in srgb, color-mix(in srgb, var(--chart-2) 55%, var(--foreground) 45%) 24%, transparent), transparent 72%), radial-gradient(40% 30% at 25% 78%, color-mix(in srgb, color-mix(in srgb, var(--accent-warm) 55%, var(--foreground) 45%) 16%, transparent), transparent 72%), radial-gradient(40% 30% at 80% 85%, color-mix(in srgb, color-mix(in srgb, var(--primary) 55%, var(--foreground) 45%) 14%, transparent), transparent 72%)",
      }}
    >
      <div
        className={cn(
          "flex flex-col gap-3 rounded-2xl p-4 sm:flex-row sm:items-center sm:justify-between",
          glassCardClassName
        )}
      >
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="text-sm text-muted-foreground">Across all properties</p>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <DateRangePicker />
          <div className="flex items-center gap-2">
            <Switch id="include-test-calls" checked={includeTestCalls} onCheckedChange={setIncludeTestCalls} />
            <Label htmlFor="include-test-calls" className="text-sm text-muted-foreground">
              Include browser test calls
            </Label>
          </div>
          {layout && !activeDraft && (
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <LayoutGrid className="size-3.5" />
              Customize
            </Button>
          )}
        </div>
      </div>

      {activeDraft && layout && (
        <OverviewCustomizer
          layout={layout}
          draft={activeDraft}
          onDraftChange={setDraft}
          onSaved={(saved) => {
            setLayout(saved);
            closeEditor();
          }}
          onCancel={closeEditor}
          onReload={() => {
            setDraft(null);
            refetchLayout();
          }}
        />
      )}

      {widgets === null || (layoutLoading && !layout && !layoutError) ? (
        <Skeleton className="h-40 w-full" />
      ) : widgets.length === 0 ? (
        <div className={cn("rounded-2xl px-5 py-8 text-center text-sm text-muted-foreground", glassCardClassName)}>
          Your Overview is empty.{" "}
          {!activeDraft && layout && (
            <button type="button" className="font-medium text-primary hover:underline" onClick={() => setEditing(true)}>
              Add widgets
            </button>
          )}
        </div>
      ) : (
        <OverviewWidgetGrid widgets={widgets} data={widgetData} />
      )}

      <LeadDetailPanel
        lead={editingLead}
        onOpenChange={(open) => !open && setEditingLead(null)}
        onSaved={refetchLeads}
      />
    </div>
  );
}
