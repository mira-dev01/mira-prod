"use client";

import { useState } from "react";
import { ArrowRight, Phone, PhoneCall, AlertTriangle, Percent, Wallet, Users } from "lucide-react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { CallsTable } from "@/components/calls-table";
import { LeadDetailPanel } from "@/components/lead-detail-panel";
import { LiveRequestsCard } from "@/components/live-requests-card";
import { OpportunitiesCard } from "@/components/opportunities-card";
import { StatCard } from "@/components/stat-card";
import { DateRangePicker } from "@/components/date-range-picker";
import { UnansweredQuestionsCard } from "@/components/unanswered-questions-card";
import { cn, glassCardClassName } from "@/lib/utils";
import type { LeadOut } from "@/lib/types";

export default function OverviewPage() {
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const { startDateISO, endDateISO } = useDateRange();

  const { data: summary, loading: summaryLoading } = useAsync(
    () => api.analytics.summary({ startDate: startDateISO, endDate: endDateISO, includeTestCalls }),
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

  const recentCalls = calls ?? [];

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
        // Same three palette tokens as before (accent-warm/primary/chart-2),
        // but each one is genuinely darkened -- mixed 65/35 with
        // --foreground to deepen the hue itself (mustard -> bronze,
        // sindoor -> oxblood, sage -> forest) -- before that darkened color
        // is blended toward transparent for the blob's softness. Reducing
        // opacity alone (the previous version) can only fade a color
        // toward the light page background, never deepen it; mixing in
        // ink first is what actually makes the same palette read darker.
        // Wide, faint --foreground wash underneath for overall depth.
        //
        // `in srgb`, not `in oklch`: confirmed live that mixing these
        // opaque warm colors (accent-warm/primary/chart-2/foreground) in
        // oklch was producing a grey/lavender cast instead of the intended
        // bronze/oxblood/forest tones -- oklch interpolation between warm
        // colors of very different lightness can dip through a
        // desaturated, hue-shifted midpoint. srgb is plain linear channel
        // averaging, which is what a simple "darken toward ink" blend
        // needs (see glassCardClassName in lib/utils.ts for the same fix).
        backgroundImage:
          "radial-gradient(70% 60% at 50% 50%, color-mix(in srgb, var(--foreground) 6%, transparent), transparent 80%), radial-gradient(50% 40% at 10% 6%, color-mix(in srgb, color-mix(in srgb, var(--accent-warm) 65%, var(--foreground) 35%) 20%, transparent), transparent 70%), radial-gradient(45% 35% at 90% 4%, color-mix(in srgb, color-mix(in srgb, var(--primary) 65%, var(--foreground) 35%) 18%, transparent), transparent 70%), radial-gradient(40% 30% at 52% 28%, color-mix(in srgb, color-mix(in srgb, var(--chart-2) 65%, var(--foreground) 35%) 18%, transparent), transparent 70%), radial-gradient(40% 30% at 22% 70%, color-mix(in srgb, color-mix(in srgb, var(--accent-warm) 65%, var(--foreground) 35%) 10%, transparent), transparent 70%), radial-gradient(45% 35% at 82% 88%, color-mix(in srgb, color-mix(in srgb, var(--primary) 65%, var(--foreground) 35%) 12%, transparent), transparent 70%)",
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
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard glass icon={Phone} label="Total calls" value={summary?.total_calls} loading={summaryLoading} />
        <StatCard
          glass
          icon={PhoneCall}
          iconColorVar="--status-live"
          label="Completed"
          value={summary?.completed_calls}
          loading={summaryLoading}
        />
        <StatCard
          glass
          icon={AlertTriangle}
          iconColorVar="--destructive"
          label="Escalated"
          value={summary?.escalated_calls}
          loading={summaryLoading}
        />
        <StatCard
          glass
          icon={Percent}
          label="Answer rate"
          value={summary?.answer_rate != null ? `${Math.round(summary.answer_rate * 100)}%` : undefined}
          loading={summaryLoading}
        />
        <StatCard
          glass
          icon={Wallet}
          label="Pipeline value"
          value={summary?.pipeline_value != null ? `₹${summary.pipeline_value.toLocaleString("en-IN")}` : undefined}
          loading={summaryLoading}
        />
        <Link href="/dashboard/leads?tab=booking&status=open" className="block">
          <StatCard
            glass
            icon={Users}
            label="Open leads"
            value={summary?.open_leads}
            loading={summaryLoading}
            interactive
          />
        </Link>
      </div>

      {/* Action-needed cards first (Live requests, then Unanswered
          questions), Recent calls last since it's passive/FYI -- same 3
          grid items and span classes as before, so the existing wrap
          tuning holds: two single-span cards pair up on row 1 at the lg
          breakpoint (1366x768/1440x900 laptop widths), Unanswered
          Questions' col-span-2 fills row 2 alone; all three sit in one row
          at xl. Reordering which card is which changes only reading order,
          not row count. */}
      <div className="grid items-stretch gap-4 lg:grid-cols-2 xl:grid-cols-3">
        {leadsLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <LiveRequestsCard
            glass
            leads={leads ?? []}
            onRefetch={refetchLeads}
            onCardClick={setEditingLead}
            limit={2}
          />
        )}

        <Card className={cn("h-full", glassCardClassName)}>
          <CardHeader>
            <CardTitle>Recent calls</CardTitle>
          </CardHeader>
          <CardContent className="flex-1">
            {callsLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : recentCalls.length === 0 ? (
              <p className="text-sm text-muted-foreground">No calls yet.</p>
            ) : (
              <CallsTable calls={recentCalls} compact />
            )}
          </CardContent>
          <div className="border-t px-4 py-3">
            <Link
              href="/dashboard/calls"
              className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              View all calls
              <ArrowRight className="size-3.5" />
            </Link>
          </div>
        </Card>

        <div className="flex lg:col-span-2 xl:col-span-1">
          <UnansweredQuestionsCard glass limit={2} linkToFaqPage hideDescription />
        </div>
      </div>

      {/* Own row, not folded into the 3-card row above -- keeps that row's
          documented wrap-behavior tuning (1366x768/1440x900 laptop widths)
          untouched. Reuses the same `leads` fetch/refetch/editingLead state
          already on this page -- no separate data fetch for this card. */}
      {leadsLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <OpportunitiesCard
          glass
          leads={leads ?? []}
          onRefetch={refetchLeads}
          onCardClick={setEditingLead}
          limit={3}
        />
      )}

      <LeadDetailPanel
        lead={editingLead}
        onOpenChange={(open) => !open && setEditingLead(null)}
        onSaved={refetchLeads}
      />
    </div>
  );
}
