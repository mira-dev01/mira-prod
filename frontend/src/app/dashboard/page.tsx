"use client";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { NotificationsFeed } from "@/components/notifications-feed";

export default function OverviewPage() {
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const { data: summary, loading: summaryLoading } = useAsync(
    () => api.analytics.summary(30, includeTestCalls),
    [includeTestCalls]
  );
  const { data: calls, loading: callsLoading } = useAsync(() => api.calls.list(), []);
  const { data: notifications, loading: notificationsLoading } = useAsync(() => api.notifications.list(), []);

  const recentCalls = calls?.slice(0, 8) ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="text-sm text-muted-foreground">Last 30 days across all properties</p>
        </div>
        <div className="flex items-center gap-2">
          <Switch id="include-test-calls" checked={includeTestCalls} onCheckedChange={setIncludeTestCalls} />
          <Label htmlFor="include-test-calls" className="text-sm text-muted-foreground">
            Include browser test calls
          </Label>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <SummaryCard label="Total calls" value={summary?.total_calls} loading={summaryLoading} />
        <SummaryCard label="Completed" value={summary?.completed_calls} loading={summaryLoading} />
        <SummaryCard label="Escalated" value={summary?.escalated_calls} loading={summaryLoading} />
        <SummaryCard
          label="Answer rate"
          value={summary?.answer_rate != null ? `${Math.round(summary.answer_rate * 100)}%` : undefined}
          loading={summaryLoading}
        />
        <SummaryCard
          label="Revenue attributed"
          value={summary?.revenue_attributed != null ? `₹${summary.revenue_attributed.toLocaleString("en-IN")}` : undefined}
          loading={summaryLoading}
        />
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
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Caller</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Started</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {recentCalls.map((call) => (
                    <TableRow key={call.id}>
                      <TableCell>{call.caller_number ?? "Unknown"}</TableCell>
                      <TableCell className="capitalize">{call.status}</TableCell>
                      <TableCell>{call.started_at ? new Date(call.started_at).toLocaleString() : "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {notificationsLoading ? <Skeleton className="h-40 w-full" /> : <NotificationsFeed initial={notifications ?? []} />}
      </div>
    </div>
  );
}

function SummaryCard({ label, value, loading }: { label: string; value?: number | string; loading: boolean }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription>{label}</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? <Skeleton className="h-7 w-16" /> : <p className="text-2xl font-semibold">{value ?? "—"}</p>}
      </CardContent>
    </Card>
  );
}
