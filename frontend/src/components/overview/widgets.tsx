"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { CallsTable } from "@/components/calls-table";
import { LiveRequestsCard } from "@/components/live-requests-card";
import { OpportunitiesCard } from "@/components/opportunities-card";
import { NeedsAttentionCard } from "@/components/overview/needs-attention";
import {
  PortfolioSnapshot,
  PortfolioSnapshotError,
  PortfolioSnapshotSkeleton,
} from "@/components/overview/portfolio-snapshot";
import { RecoveryAnalyticsCard } from "@/components/recovery-analytics-card";
import { UnansweredQuestionsCard } from "@/components/unanswered-questions-card";
import { cn, glassCardClassName } from "@/lib/utils";
import type { CallSessionOut, LeadOut, OverviewData, OverviewWidget } from "@/lib/types";

/** Everything the Overview page already fetched, handed to each widget
 * unchanged -- widgets never fetch differently from how the page did. */
export type OverviewWidgetData = {
  overview: OverviewData | null;
  overviewLoading: boolean;
  overviewError: Error | null;
  refetchOverview: () => void;
  calls: CallSessionOut[] | null;
  callsLoading: boolean;
  leads: LeadOut[] | null;
  leadsLoading: boolean;
  refetchLeads: () => void;
  onLeadClick: (lead: LeadOut) => void;
};

// One renderer per widget id from the backend registry
// (app/services/overview_widget_registry.py). Each body is the exact
// component/markup the Overview page rendered before it became
// customizable. An id with no renderer is skipped, never rendered broken.
export const WIDGET_RENDERERS: Record<string, (d: OverviewWidgetData) => React.ReactNode> = {
  portfolio_snapshot: (d) =>
    d.overviewError && !d.overview ? (
      <PortfolioSnapshotError onRetry={d.refetchOverview} />
    ) : d.overviewLoading && !d.overview ? (
      <PortfolioSnapshotSkeleton />
    ) : d.overview ? (
      <PortfolioSnapshot data={d.overview} />
    ) : null,

  needs_attention: (d) =>
    d.overviewError && !d.overview ? (
      <p className={cn("rounded-2xl px-5 py-3.5 text-sm text-muted-foreground", glassCardClassName)}>
        <span className="font-medium text-foreground">Needs your attention</span> · Couldn&apos;t load this right
        now.
      </p>
    ) : (
      <NeedsAttentionCard data={d.overview} loading={d.overviewLoading && !d.overview} onChanged={d.refetchOverview} />
    ),

  live_requests: (d) =>
    d.leadsLoading ? (
      <Skeleton className="h-40 w-full" />
    ) : (
      <LiveRequestsCard glass leads={d.leads ?? []} onRefetch={d.refetchLeads} onCardClick={d.onLeadClick} limit={3} />
    ),

  opportunities: (d) =>
    d.leadsLoading ? (
      <Skeleton className="h-40 w-full" />
    ) : (
      <OpportunitiesCard glass leads={d.leads ?? []} onRefetch={d.refetchLeads} onCardClick={d.onLeadClick} limit={3} />
    ),

  recent_calls: (d) => (
    // Recent calls follow the selected period.
    <Card className={cn("h-full", glassCardClassName)}>
      <CardHeader>
        <CardTitle>Recent calls</CardTitle>
      </CardHeader>
      <CardContent className="flex-1">
        {d.callsLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : (d.calls ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No calls in this period.</p>
        ) : (
          <CallsTable calls={d.calls ?? []} compact />
        )}
      </CardContent>
      <div className="border-t px-4 py-3">
        <Link href="/dashboard/calls" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          View all calls
          <ArrowRight className="size-3.5" />
        </Link>
      </div>
    </Card>
  ),

  // Current-state, not tied to the date range (unchanged).
  unanswered_questions: () => (
    <div className="flex h-full min-w-0">
      <UnansweredQuestionsCard glass limit={2} linkToFaqPage hideDescription />
    </div>
  ),

  // Same self-contained card the Opportunities page shows (follows the
  // header date range).
  busy_call_recovery: () => <RecoveryAnalyticsCard />,
};

function fallback(
  id: string,
  name: string,
  section: OverviewWidget["section"],
  size: OverviewWidget["size"],
  sizes: OverviewWidget["sizes"],
  visible = true
): OverviewWidget {
  return {
    id,
    name,
    description: "",
    capabilities: [],
    sizes,
    size,
    hidden: !visible,
    hideable: true,
    default_visible: visible,
    available: true,
    section,
    unavailable_reason: null,
  };
}

// The registry's recommended layout -- used if GET /preferences/overview-
// widgets fails (so the Overview still renders exactly as it always did)
// and as the "Recommended layout" draft in the customizer.
// backend/tests/test_ui_preferences.py keeps these ids in sync.
export const FALLBACK_WIDGETS: OverviewWidget[] = [
  fallback("portfolio_snapshot", "Portfolio snapshot", "summary", "full", ["full"]),
  fallback("needs_attention", "Needs your attention", "summary", "full", ["full"]),
  fallback("live_requests", "Live requests", "live", "half", ["half", "full"]),
  fallback("opportunities", "Opportunities", "live", "half", ["half", "full"]),
  fallback("recent_calls", "Recent calls", "details", "half", ["half", "full"]),
  fallback("unanswered_questions", "Unanswered questions", "details", "half", ["half", "full"]),
  fallback("busy_call_recovery", "Busy-call recovery", "details", "half", ["half", "full"], false),
];

const SECTION_LABELS: Record<OverviewWidget["section"], string | null> = {
  summary: null,
  live: "Live operations",
  details: "Details",
};

/**
 * Renders widgets in order, grouping consecutive widgets of the same
 * section under that section's heading -- which, for the default order,
 * reproduces the original Overview exactly (snapshot, needs-attention,
 * "Live operations", "Details"). Only predefined sizes exist: "full" spans
 * both columns of the existing lg:grid-cols-2 grid, "half" spans one.
 */
export function OverviewWidgetGrid({ widgets, data }: { widgets: OverviewWidget[]; data: OverviewWidgetData }) {
  const renderable = widgets.filter((w) => WIDGET_RENDERERS[w.id]);
  const runs: { section: OverviewWidget["section"]; widgets: OverviewWidget[] }[] = [];
  for (const widget of renderable) {
    const last = runs[runs.length - 1];
    if (last && last.section === widget.section) last.widgets.push(widget);
    else runs.push({ section: widget.section, widgets: [widget] });
  }

  return (
    <>
      {runs.map((run, runIndex) => {
        const label = SECTION_LABELS[run.section];
        const grid = (
          <div className="grid items-stretch gap-4 lg:grid-cols-2">
            {run.widgets.map((w) => (
              <div key={w.id} className={cn("min-w-0", w.size === "full" && "lg:col-span-2")}>
                {WIDGET_RENDERERS[w.id](data)}
              </div>
            ))}
          </div>
        );
        if (!label) {
          // Summary widgets are full-width only and keep the page's own
          // vertical rhythm (space-y-5), as before.
          return run.widgets.map((w) => <div key={w.id}>{WIDGET_RENDERERS[w.id](data)}</div>);
        }
        const headingId = `overview-${run.section}-${runIndex}`;
        return (
          <section key={headingId} aria-labelledby={headingId} className="space-y-3 pt-2">
            <h2 id={headingId} className="px-1 text-sm font-medium text-muted-foreground">
              {label}
            </h2>
            {grid}
          </section>
        );
      })}
    </>
  );
}
