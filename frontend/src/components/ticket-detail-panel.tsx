"use client";

import { useState } from "react";
import Link from "next/link";
import { Check } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { RightPanel } from "@/components/ui/right-panel";
import { SpacezStayRequestActions } from "@/components/spacez-stay-request-actions";
import { StatusChip } from "@/components/status-chip";
import { KIND_LABEL, PHASE_LABEL, ResolutionTimer } from "@/components/ticket-card";
import { TicketTranscript } from "@/components/ticket-transcript-panel";
import { WhatsAppButton } from "@/components/whatsapp-button";
import { api, ApiError } from "@/lib/api";
import { formatLeadTimestamp } from "@/lib/leads";
import { KIND_TONE, ticketCode, ticketDetailText, ticketPromises } from "@/lib/tickets";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { TicketOut } from "@/lib/types";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

/**
 * A ticket opened from the Tickets list: what it is, who it is for, what Mira promised, how long it has waited, the
 * actions (done, dismiss, reopen), and the call behind it with the lines where Mira escalated highlighted.
 */
export function TicketDetailPanel({
  ticket,
  onOpenChange,
  onChanged,
}: {
  ticket: TicketOut | null;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function setStatus(status: "open" | "done" | "dismissed") {
    if (!ticket) return;
    setBusy(true);
    try {
      await api.tickets.setStatus(ticket.id, status);
      onChanged();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not update the ticket");
    } finally {
      setBusy(false);
    }
  }

  const phone = ticket && !isBrowserTestIdentity(ticket.guest_phone) ? ticket.guest_phone : null;
  const promises = ticket ? ticketPromises(ticket) : [];

  return (
    <RightPanel open={ticket !== null} onOpenChange={onOpenChange} size="lg" title={ticket ? <span className="font-mono">{ticketCode(ticket)}</span> : "Ticket"}>
      {ticket && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip status={KIND_LABEL[ticket.kind]} tone={KIND_TONE[ticket.kind]} />
            {ticket.booking_phase && (
              <StatusChip status={PHASE_LABEL[ticket.booking_phase]} tone="live" className="normal-case" />
            )}
            {(ticket.urgency === "high" || ticket.urgency === "emergency") && (
              <StatusChip status={ticket.urgency} tone={ticket.urgency === "emergency" ? "destructive" : "orange"} />
            )}
            {ticket.status !== "open" && <StatusChip status={ticket.status === "done" ? "Done" : "Dismissed"} tone="neutral" className="normal-case" />}
          </div>

          <div className="space-y-2">
            <h2 className="text-base leading-snug font-semibold">{ticket.summary}</h2>
            {ticketDetailText(ticket) && <p className="text-sm leading-relaxed whitespace-pre-line text-muted-foreground">{ticketDetailText(ticket)}</p>}
          </div>

          {promises.length > 0 && (
            <section className="space-y-1.5">
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">What Mira promised</h3>
              <ul className="space-y-1 border-l-2 border-primary pl-3 text-sm">
                {promises.map((promise) => (
                  <li key={promise}>{promise}</li>
                ))}
              </ul>
            </section>
          )}

          <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
            <Fact label="Guest">{ticket.guest_name ?? "Unknown guest"}</Fact>
            <Fact label="Phone">
              {phone ? (
                <span className="flex items-center gap-2">
                  <span className="font-mono">{phone}</span>
                  <WhatsAppButton phone={phone} />
                </span>
              ) : (
                "—"
              )}
            </Fact>
            <Fact label="Property">{ticket.property_name ?? "—"}</Fact>
            <Fact label="Created">{formatLeadTimestamp(ticket.created_at)}</Fact>
            <Fact label={ticket.status === "open" ? "Open for" : "Time taken"}>
              <ResolutionTimer ticket={ticket} />
            </Fact>
            {ticket.resolved_at && <Fact label="Closed">{formatLeadTimestamp(ticket.resolved_at)}</Fact>}
          </dl>

          <div className="flex flex-wrap justify-end gap-2">
            {ticket.status === "open" && ticket.kind === "stay_request" ? (
              <SpacezStayRequestActions ticketId={ticket.id} onAnswered={onChanged} />
            ) : ticket.status === "open" ? (
              <>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setStatus("dismissed")}>
                  Dismiss
                </Button>
                <Button size="sm" disabled={busy} onClick={() => setStatus("done")}>
                  <Check className="size-4" />
                  Done
                </Button>
              </>
            ) : (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => setStatus("open")}>
                Reopen
              </Button>
            )}
          </div>

          {ticket.call_session_id && (
            <section className="space-y-2 border-t border-border pt-4">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Call transcript</h3>
                <Link href={`/dashboard/calls/${ticket.call_session_id}`} className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground">
                  View call
                </Link>
              </div>
              <TicketTranscript callSessionId={ticket.call_session_id} guestName={ticket.guest_name} />
            </section>
          )}
        </>
      )}
    </RightPanel>
  );
}
