"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { cn, isBrowserTestIdentity } from "@/lib/utils";

const statusVariant: Record<string, "destructive" | "outline"> = {
  completed: "outline",
  active: "outline",
  escalated: "destructive",
  failed: "outline",
};

const statusClassName: Record<string, string> = {
  completed: "badge-status-live",
  active: "badge-status-progress",
};

export default function CallsPage() {
  const { data: calls, loading } = useAsync(() => api.calls.list(), []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">Calls</h1>
        <p className="text-sm text-muted-foreground">Every call MIRA has answered across your properties</p>
      </div>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : !calls || calls.length === 0 ? (
        <p className="text-sm text-muted-foreground">No calls yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Caller</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Urgency</TableHead>
              <TableHead>Revenue</TableHead>
              <TableHead>Started</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {calls.map((call) => (
              <TableRow key={call.id} className="cursor-pointer">
                <TableCell>
                  <Link href={`/dashboard/calls/${call.id}`} className="hover:underline">
                    {isBrowserTestIdentity(call.caller_number) ? (
                      <Badge variant="outline">Browser test</Badge>
                    ) : (
                      call.caller_number ?? "Unknown"
                    )}
                  </Link>
                </TableCell>
                <TableCell>
                  <Badge
                    variant={statusVariant[call.status] ?? "outline"}
                    className={cn("capitalize", statusClassName[call.status])}
                  >
                    {call.status}
                  </Badge>
                </TableCell>
                <TableCell className="capitalize">{call.urgency ?? "—"}</TableCell>
                <TableCell>₹{call.revenue_attributed.toLocaleString("en-IN")}</TableCell>
                <TableCell>{call.started_at ? new Date(call.started_at).toLocaleString() : "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
