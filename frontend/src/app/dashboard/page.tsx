"use client";

import { useState } from "react";
import { ArrowRight, Phone, PhoneCall, AlertTriangle, Percent, Wallet, Users } from "lucide-react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { CallsTable } from "@/components/calls-table";
import { LeadDetailPanel } from "@/components/lead-detail-panel";
import { LiveRequestsCard } from "@/components/live-requests-card";
import { StatCard } from "@/components/stat-card";
import { DateRangePicker } from "@/components/date-range-picker";
import { UnansweredQuestionsCard } from "@/components/unanswered-questions-card";
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
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
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

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard icon={Phone} label="Total calls" value={summary?.total_calls} loading={summaryLoading} />
        <StatCard
          icon={PhoneCall}
          iconColorVar="--status-live"
          label="Completed"
          value={summary?.completed_calls}
          loading={summaryLoading}
        />
        <StatCard
          icon={AlertTriangle}
          iconColorVar="--destructive"
          label="Escalated"
          value={summary?.escalated_calls}
          loading={summaryLoading}
        />
        <StatCard
          icon={Percent}
          label="Answer rate"
          value={summary?.answer_rate != null ? `${Math.round(summary.answer_rate * 100)}%` : undefined}
          loading={summaryLoading}
        />
        <StatCard
          icon={Wallet}
          label="Pipeline value"
          value={summary?.pipeline_value != null ? `₹${summary.pipeline_value.toLocaleString("en-IN")}` : undefined}
          loading={summaryLoading}
        />
        <Link href="/dashboard/leads?tab=booking&status=open" className="block">
          <StatCard icon={Users} label="Open leads" value={summary?.open_leads} loading={summaryLoading} interactive />
        </Link>
      </div>

      {/* Live requests and unanswered FAQs both need to be visible at first
          glance without scrolling -- xl:grid-cols-3 pulls Unanswered
          Questions into the same row on wide screens; on narrower laptop
          widths (1366x768/1440x900) it wraps to a second row but stays
          right below the fold rather than pushed down by an 8-row calls
          table (cut to 5 here, full list stays one click away). */}
      <div className="grid items-stretch gap-6 lg:grid-cols-2 xl:grid-cols-3">
        <Card className="h-full">
          <CardHeader>
            <CardTitle>Recent calls</CardTitle>
            <CardDescription>Latest 5 call sessions across your properties</CardDescription>
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

        {leadsLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <LiveRequestsCard
            leads={leads ?? []}
            onRefetch={refetchLeads}
            onCardClick={setEditingLead}
            limit={3}
          />
        )}

        <div className="flex lg:col-span-2 xl:col-span-1">
          <UnansweredQuestionsCard limit={2} linkToFaqPage />
        </div>
      </div>

      <LeadDetailPanel
        lead={editingLead}
        onOpenChange={(open) => !open && setEditingLead(null)}
        onSaved={refetchLeads}
      />
    </div>
  );
}
