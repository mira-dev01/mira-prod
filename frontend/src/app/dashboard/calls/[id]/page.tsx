"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { parseTranscript } from "@/lib/transcript";
import { cn, isBrowserTestIdentity } from "@/lib/utils";

const statusTone: Record<string, StatusTone> = {
  completed: "live",
  active: "progress",
  in_progress: "progress",
  escalated: "destructive",
  failed: "destructive",
  missed: "destructive",
};

// Same urgency vocabulary/tones as calls/page.tsx's urgencyTone -- kept
// local to each page rather than shared, consistent with how statusTone is
// already duplicated per-page in this codebase.
const urgencyTone: Record<string, StatusTone> = {
  emergency: "destructive",
  high: "destructive",
  medium: "pending",
  low: "neutral",
};

function formatDuration(minutes: number | null): string | null {
  if (minutes === null) return null;
  const whole = Math.floor(minutes);
  const seconds = Math.round((minutes - whole) * 60);
  return `${whole}m ${seconds}s`;
}

export default function CallDetailPage() {
  const params = useParams<{ id: string }>();
  const { data: call, loading, refetch } = useAsync(() => api.calls.get(params.id), [params.id]);

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (!call) return <p className="text-sm text-muted-foreground">Call not found.</p>;

  const isTest = isBrowserTestIdentity(call.caller_number);
  const duration = formatDuration(call.duration_minutes);
  const turns = call.transcript ? parseTranscript(call.transcript) : null;
  const openEscalations = call.escalations.filter((e) => e.status === "new");

  async function handleMarkHandled(notificationId: string) {
    try {
      await api.notifications.markRead(notificationId);
      toast.success("Marked as handled");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">
            {isTest ? "Browser test" : call.guest_name ?? call.caller_number ?? "Unknown caller"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {call.started_at ? new Date(call.started_at).toLocaleString() : "—"}
            {!isTest && call.guest_phone ? ` · ${call.guest_phone}` : ""}
          </p>
        </div>
        <Button variant="outline" render={<Link href="/dashboard/calls" />}>
          Back to calls
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <StatusChip status={call.status} tone={statusTone[call.status] ?? "neutral"} />
        {call.urgency && <StatusChip status={call.urgency} tone={urgencyTone[call.urgency] ?? "neutral"} />}
        {duration && <Badge variant="secondary">{duration}</Badge>}
      </div>

      {/* AI summary promoted above the transcript -- the CTA the host sees
          first, styled distinctly (surface-interactive) so it reads as the
          primary artifact of the call rather than just another card. */}
      <Card className="surface-interactive border-primary/20 bg-primary/5">
        <CardHeader>
          <CardTitle>AI summary</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-base leading-relaxed">{call.ai_summary ?? "No summary available."}</p>
        </CardContent>
      </Card>

      {call.escalations.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              Escalations
              {openEscalations.length > 0 && <Badge variant="destructive">{openEscalations.length} open</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {call.escalations.map((escalation) => (
              <div key={escalation.id} className="space-y-2 rounded-lg border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusChip status={escalation.urgency} tone={urgencyTone[escalation.urgency] ?? "neutral"} />
                    <StatusChip
                      status={escalation.status === "new" ? "open" : "handled"}
                      tone={escalation.status === "new" ? "pending" : "live"}
                    />
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {new Date(escalation.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="text-sm leading-relaxed">{escalation.message}</p>
                {escalation.status === "new" && (
                  <Button size="sm" onClick={() => handleMarkHandled(escalation.id)}>
                    Mark as handled
                  </Button>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Transcript</CardTitle>
        </CardHeader>
        <CardContent>
          {!call.transcript ? (
            <p className="text-sm text-muted-foreground">No transcript available.</p>
          ) : turns ? (
            <div className="space-y-3">
              {turns.map((turn, i) => (
                <div key={i} className={cn("flex", turn.role === "mira" ? "justify-start" : "justify-end")}>
                  <div
                    className={cn(
                      "max-w-[80%] rounded-lg px-3 py-2 text-sm leading-relaxed",
                      turn.role === "mira" ? "bg-muted text-foreground" : "bg-primary text-primary-foreground"
                    )}
                  >
                    <p className="mb-0.5 text-[0.7rem] font-medium opacity-70">
                      {turn.role === "mira" ? "Mira" : isTest ? "Browser test" : call.guest_name ?? "Guest"}
                    </p>
                    {turn.text}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <pre className="whitespace-pre-wrap text-sm text-muted-foreground">{call.transcript}</pre>
          )}
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
