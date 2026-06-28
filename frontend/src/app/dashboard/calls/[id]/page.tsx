"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { isBrowserTestIdentity } from "@/lib/utils";

export default function CallDetailPage() {
  const params = useParams<{ id: string }>();
  const { data: call, loading } = useAsync(() => api.calls.get(params.id), [params.id]);

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (!call) return <p className="text-sm text-muted-foreground">Call not found.</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">
            {isBrowserTestIdentity(call.caller_number) ? "Browser test" : call.caller_number ?? "Unknown caller"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {call.started_at ? new Date(call.started_at).toLocaleString() : "—"}
          </p>
        </div>
        <Button variant="outline" render={<Link href="/dashboard/calls" />}>
          Back to calls
        </Button>
      </div>

      <div className="flex gap-2">
        <Badge className="capitalize">{call.status}</Badge>
        {call.urgency && <Badge variant="destructive" className="capitalize">{call.urgency}</Badge>}
        <Badge variant="secondary">₹{call.revenue_attributed.toLocaleString("en-IN")} attributed</Badge>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>AI summary</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm">{call.ai_summary ?? "No summary available."}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Transcript</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="whitespace-pre-wrap text-sm text-muted-foreground">
            {call.transcript ?? "No transcript available."}
          </pre>
        </CardContent>
      </Card>

      {call.recording_url && (
        <Card>
          <CardHeader>
            <CardTitle>Recording</CardTitle>
          </CardHeader>
          <CardContent>
            <audio controls className="w-full" src={call.recording_url} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
