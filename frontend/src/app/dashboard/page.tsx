"use client";

import { useState } from "react";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { CardContent, CardHeader } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Cell, CellCard, CellTitle, Sheet } from "@/components/ui/sheet";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { CallsTable } from "@/components/calls-table";
import { LeadDetailPanel } from "@/components/lead-detail-panel";
import { LiveRequestsCard } from "@/components/live-requests-card";
import { OpportunitiesCard } from "@/components/opportunities-card";
import { FirstRunChecklist } from "@/components/overview/first-run-checklist";
import { NeedsAttentionCard } from "@/components/overview/needs-attention";
import {
  PortfolioSnapshot,
  PortfolioSnapshotError,
  PortfolioSnapshotSkeleton,
} from "@/components/overview/portfolio-snapshot";
import { DateRangePicker } from "@/components/date-range-picker";
import { UnansweredQuestionsCard } from "@/components/unanswered-questions-card";
import { USE_CASES } from "@/lib/use-cases";
import type { LeadOut } from "@/lib/types";

export default function OverviewPage() {
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const { startDateISO, endDateISO } = useDateRange();
  const { user } = useAuth();
  const interests = user?.interests ?? [];
  // Leads renders before Missed calls by default; swap when the host picked
  // whatsapp_automation (Missed calls' use case) ahead of inbound_leads in
  // onboarding -- same pick-order convention sidebar-nav.tsx uses.
  const opportunitiesFirst =
    interests.includes("whatsapp_automation") &&
    (!interests.includes("inbound_leads") ||
      interests.indexOf("whatsapp_automation") < interests.indexOf("inbound_leads"));

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

  const recentCalls = calls ?? [];

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
                  className="border border-foreground bg-card/70 px-3 py-1 text-xs font-semibold text-foreground transition-colors hover:bg-accent"
                >
                  {useCase.label}
                </Link>
              ))}
            </div>
          )}

          <Sheet>
            {overviewError && !overview ? (
              <PortfolioSnapshotError onRetry={refetchOverview} />
            ) : overviewLoading && !overview ? (
              <PortfolioSnapshotSkeleton />
            ) : overview ? (
              <PortfolioSnapshot data={overview} />
            ) : null}

            {overviewError && !overview ? (
              <Cell className="text-sm text-muted-foreground">
                <span>
                  <span className="font-semibold text-foreground">Needs your attention</span> · Couldn&apos;t load.
                </span>
              </Cell>
            ) : (
              <NeedsAttentionCard data={overview} loading={overviewLoading && !overview} onChanged={refetchOverview} />
            )}

            {leadsLoading ? (
              <>
                <Cell className="md:col-span-6">
                  <Skeleton className="h-32 w-full" />
                </Cell>
                <Cell className="md:col-span-6">
                  <Skeleton className="h-32 w-full" />
                </Cell>
              </>
            ) : (
              (() => {
                const liveRequestsCard = (
                  <LiveRequestsCard
                    key="leads"
                    leads={leads ?? []}
                    onRefetch={refetchLeads}
                    onCardClick={setEditingLead}
                    limit={3}
                  />
                );
                const opportunitiesCard = (
                  <OpportunitiesCard
                    key="opportunities"
                    leads={leads ?? []}
                    onRefetch={refetchLeads}
                    onCardClick={setEditingLead}
                    limit={3}
                  />
                );
                return opportunitiesFirst ? [opportunitiesCard, liveRequestsCard] : [liveRequestsCard, opportunitiesCard];
              })()
            )}

            {/* Recent calls follow the selected period; unanswered questions are current-state. */}
            <CellCard className="md:col-span-6">
              <CardHeader>
                <CellTitle>Recent calls</CellTitle>
              </CardHeader>
              <CardContent className="flex-1">
                {callsLoading ? (
                  <Skeleton className="h-40 w-full" />
                ) : recentCalls.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No calls in this period.</p>
                ) : (
                  <CallsTable calls={recentCalls} compact />
                )}
              </CardContent>
              <div className="border-t border-border px-5 py-3">
                <Link
                  href="/dashboard/calls"
                  className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                >
                  View all calls
                  <ArrowRight className="size-3.5" />
                </Link>
              </div>
            </CellCard>

            <UnansweredQuestionsCard inSheet limit={2} linkToFaqPage hideDescription />
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
