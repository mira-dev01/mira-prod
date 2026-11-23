import { TriangleAlert } from "lucide-react";
import { markEscalations, parseTranscript } from "@/lib/transcript";
import { cn } from "@/lib/utils";
import type { EscalationMarker } from "@/lib/types";

/**
 * A call's transcript as a conversation. The lines where Mira escalated to the host (the guest's ask and Mira's
 * reply) are set apart in the brand red with an "Escalated to host" label, so a host reading a ticket's call can
 * see at once what led to it. `markers` come from the call itself (null for a call from before they were kept, then
 * Mira's own wording is recognised instead).
 */
export function TranscriptView({
  transcript,
  markers,
  guestLabel,
}: {
  transcript: string | null;
  markers: EscalationMarker[] | null;
  guestLabel: string;
}) {
  if (!transcript) return <p className="text-sm text-muted-foreground">No transcript available.</p>;
  const parsed = parseTranscript(transcript);
  if (!parsed) return <pre className="whitespace-pre-wrap text-sm text-muted-foreground">{transcript}</pre>;
  const { turns, recorded } = markEscalations(parsed, markers);
  const anyEscalation = turns.some((turn) => turn.escalation);

  return (
    <div className="space-y-3">
      {anyEscalation && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="inline-block size-2.5 rounded-sm border border-destructive bg-[color-mix(in_srgb,var(--destructive)_16%,transparent)]" />
          Where Mira escalated to the host{recorded ? "" : " (picked out from her wording)"}
        </p>
      )}
      {turns.map((turn, i) =>
        turn.unclear ? (
          <p key={i} className="text-xs italic text-muted-foreground">
            (unclear audio)
          </p>
        ) : (
          <div key={i} className={cn("flex", turn.role === "mira" ? "justify-end" : "justify-start")}>
            <div
              data-escalation={turn.escalation ? "true" : undefined}
              className={cn(
                "max-w-[80%] whitespace-pre-wrap px-3 py-2 text-sm leading-relaxed",
                turn.escalation
                  ? "border border-destructive bg-[color-mix(in_srgb,var(--destructive)_14%,transparent)] text-foreground"
                  : turn.role === "mira"
                    ? "border border-border bg-muted text-foreground"
                    : "border border-(--status-orange) bg-[color-mix(in_srgb,var(--status-orange)_14%,transparent)] text-foreground"
              )}
            >
              <p className="mb-0.5 flex items-center gap-1.5 text-xs font-semibold opacity-80">
                {turn.role === "mira" ? "Mira" : guestLabel}
                {turn.escalation && (
                  <span className="inline-flex items-center gap-1 text-destructive">
                    <TriangleAlert className="size-3" />
                    Escalated to host
                  </span>
                )}
              </p>
              {turn.text}
            </div>
          </div>
        )
      )}
    </div>
  );
}
