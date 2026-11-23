"use client";

import { RightPanel } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { TranscriptView } from "@/components/transcript-view";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { isBrowserTestIdentity } from "@/lib/utils";

/** The call behind a ticket, opened beside the list: its transcript with the escalation lines highlighted. */
export function TicketTranscriptPanel({
  callSessionId,
  guestName,
  onOpenChange,
}: {
  callSessionId: string | null;
  guestName: string | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: call, loading, error } = useAsync(
    () => (callSessionId ? api.calls.get(callSessionId) : Promise.resolve(null)),
    [callSessionId]
  );
  const label = call && isBrowserTestIdentity(call.caller_number) ? "Browser test" : call?.guest_name ?? guestName ?? "Guest";

  return (
    <RightPanel open={!!callSessionId} onOpenChange={onOpenChange} size="lg" title={<>Call transcript{guestName ? ` — ${guestName}` : ""}</>}>
      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : error || !call ? (
        <p className="text-sm text-muted-foreground">Could not load this call.</p>
      ) : (
        <TranscriptView transcript={call.transcript} markers={call.escalation_markers} guestLabel={label} />
      )}
    </RightPanel>
  );
}
