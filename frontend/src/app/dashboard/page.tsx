"use client";

import { useState } from "react";
import { ArrowRight, ChevronRight, Phone, PhoneCall, AlertTriangle, Percent, Wallet, Users } from "lucide-react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { NotificationsFeed } from "@/components/notifications-feed";
import { StatCard } from "@/components/stat-card";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { DateRangePicker } from "@/components/date-range-picker";
import { UnansweredQuestionsCard } from "@/components/unanswered-questions-card";

const statusTone: Record<string, StatusTone> = {
  completed: "live",
  in_progress: "progress",
  active: "progress",
  escalated: "destructive",
  failed: "destructive",
  missed: "destructive",
  pending: "pending",
};

export default function OverviewPage() {
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const { startDateISO, endDateISO } = useDateRange();

  const { data: summary, loading: summaryLoading } = useAsync(
    () => api.analytics.summary({ startDate: startDateISO, endDate: endDateISO, includeTestCalls }),
    [startDateISO, endDateISO, includeTestCalls]
  );
  const { data: calls, loading: callsLoading } = useAsync(
    () => api.calls.list({ startDate: startDateISO, endDate: endDateISO, limit: 8, includeTestCalls }),
    [startDateISO, endDateISO, includeTestCalls]
  );
  const { data: notifications, loading: notificationsLoading } = useAsync(() => api.notifications.list(), []);

  const recentCalls = calls ?? [];
  const activeNotifications = (notifications ?? []).filter((n) => n.status === "new");

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
        <Link href="/dashboard/leads?status=open" className="block">
          <StatCard icon={Users} label="Open leads" value={summary?.open_leads} loading={summaryLoading} interactive />
        </Link>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent calls</CardTitle>
            <CardDescription>Latest 8 call sessions across your properties</CardDescription>
          </CardHeader>
          <CardContent>
            {callsLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : recentCalls.length === 0 ? (
              <p className="text-sm text-muted-foreground">No calls yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Caller</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Started</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {recentCalls.map((call) => (
                      <TableRow key={call.id}>
                        <TableCell>{call.caller_number ?? "Unknown"}</TableCell>
                        <TableCell>
                          <StatusChip status={call.status} tone={statusTone[call.status] ?? "neutral"} />
                        </TableCell>
                        <TableCell>{call.started_at ? new Date(call.started_at).toLocaleString() : "—"}</TableCell>
                        <TableCell>
                          <ChevronRight className="size-4 text-muted-foreground" />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
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

        {notificationsLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <NotificationsFeed initial={notifications ?? []} activeCount={activeNotifications.length} />
        )}
      </div>

      <UnansweredQuestionsCard limit={2} linkToFaqPage />
    </div>
  );
}
