"use client";

import { RightPanel } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { TranscriptView } from "@/components/transcript-view";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { isBrowserTestIdentity } from "@/lib/utils";

/** The call behind a ticket: its transcript with the escalation lines highlighted. */
export function TicketTranscript({ callSessionId, guestName }: { callSessionId: string | null; guestName: string | null }) {
  const { data: call, loading, error } = useAsync(
    () => (callSessionId ? api.calls.get(callSessionId) : Promise.resolve(null)),
    [callSessionId]
  );
  const label = call && isBrowserTestIdentity(call.caller_number) ? "Browser test" : call?.guest_name ?? guestName ?? "Guest";
  if (loading) return <Skeleton className="h-64 w-full" />;
  if (error || !call) return <p className="text-sm text-muted-foreground">Could not load this call.</p>;
  return <TranscriptView transcript={call.transcript} markers={call.escalation_markers} guestLabel={label} />;
}

/** The same transcript, opened by itself in a panel beside the list (a ticket on a checked-in guest's row). */
export function TicketTranscriptPanel({
  callSessionId,
  guestName,
  onOpenChange,
}: {
  callSessionId: string | null;
  guestName: string | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <RightPanel open={!!callSessionId} onOpenChange={onOpenChange} size="lg" title={<>Call transcript{guestName ? ` — ${guestName}` : ""}</>}>
      <TicketTranscript callSessionId={callSessionId} guestName={guestName} />
    </RightPanel>
  );
}
