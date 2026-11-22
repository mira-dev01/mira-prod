"use client";

import { useState } from "react";
import Link from "next/link";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Cell, Sheet } from "@/components/ui/sheet";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { LeadDetailPanel } from "@/components/lead-detail-panel";
import { FirstRunChecklist } from "@/components/overview/first-run-checklist";
import { NeedsAttentionCard } from "@/components/overview/needs-attention";
import { KpiRow, KpiRowError, KpiRowSkeleton } from "@/components/overview/kpi-row";
import { FunnelCard } from "@/components/overview/funnel-card";
import { LeadsCard } from "@/components/overview/leads-card";
import { RecentCallsCard } from "@/components/overview/recent-calls-card";
import { DateRangePicker } from "@/components/date-range-picker";
import { UnansweredQuestionsCard } from "@/components/unanswered-questions-card";
import { USE_CASES } from "@/lib/use-cases";
import type { LeadOut } from "@/lib/types";

export default function OverviewPage() {
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const { startDateISO, endDateISO } = useDateRange();
  const { user } = useAuth();
  const interests = user?.interests ?? [];

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
  // Unfiltered by date range -- Leads is "what's open right now," not a
  // report scoped to the header's date picker.
  const { data: leads, loading: leadsLoading, refetch: refetchLeads } = useAsync(() => api.leads.list({}), []);
  const [editingLead, setEditingLead] = useState<LeadOut | null>(null);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <h1 className="page-title">Overview</h1>
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

      {overview?.has_properties === false ? (
        // True first-run: every block below has its own empty state, so a host
        // with zero properties would see a page of empty blocks. Show the short
        // checklist instead.
        <Sheet>
          <FirstRunChecklist name={user?.name} interests={interests} />
        </Sheet>
      ) : (
        <>
          {interests.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-bold tracking-[0.1em] text-muted-foreground uppercase">Your focus</span>
              {USE_CASES.filter((useCase) => interests.includes(useCase.key)).map((useCase) => (
                <Link
                  key={useCase.key}
                  href={useCase.navHref}
                  className="rounded-full border border-border bg-card px-3 py-1 text-xs font-semibold text-foreground transition-colors hover:bg-accent"
                >
                  {useCase.label}
                </Link>
              ))}
            </div>
          )}

          {overviewError && !overview ? (
            <KpiRowError onRetry={refetchOverview} />
          ) : overviewLoading && !overview ? (
            <KpiRowSkeleton />
          ) : overview ? (
            <KpiRow data={overview} />
          ) : null}

          <Sheet>
            <FunnelCard
              stages={overview?.funnel}
              info={overview?.info.booking_funnel}
              loading={overviewLoading && !overview}
              className="md:col-span-5"
            />
            <LeadsCard
              leads={leads ?? []}
              info={overview?.info.leads}
              loading={leadsLoading}
              onOpenLead={setEditingLead}
              className="md:col-span-7"
            />
            <RecentCallsCard
              calls={calls ?? []}
              info={overview?.info.recent_calls}
              loading={callsLoading}
              className="md:col-span-7"
            />
            {overviewError && !overview ? (
              <Cell className="text-sm text-muted-foreground md:col-span-5">
                <span>
                  <span className="font-semibold text-foreground">Needs your attention</span> · Couldn&apos;t load.
                </span>
              </Cell>
            ) : (
              <NeedsAttentionCard data={overview} loading={overviewLoading && !overview} onChanged={refetchOverview} />
            )}
            <UnansweredQuestionsCard inSheet limit={4} linkToFaqPage info={overview?.info.unanswered_questions} />
          </Sheet>
        </>
      )}

      <LeadDetailPanel
        lead={editingLead}
        onOpenChange={(open) => !open && setEditingLead(null)}
        onSaved={refetchLeads}
      />
    </div>
  );
}
