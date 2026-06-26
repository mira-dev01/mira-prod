"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";

const statusVariant: Record<string, "default" | "destructive" | "secondary" | "outline"> = {
  completed: "secondary",
  active: "default",
  escalated: "destructive",
  failed: "outline",
};

export default function CallsPage() {
  const { data: calls, loading } = useAsync(() => api.calls.list(), []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Calls</h1>
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
                    {call.caller_number ?? "Unknown"}
                  </Link>
                </TableCell>
                <TableCell>
                  <Badge variant={statusVariant[call.status] ?? "outline"} className="capitalize">
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
