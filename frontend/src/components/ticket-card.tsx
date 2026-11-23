"use client";

import { useState } from "react";
import Link from "next/link";
import { FileText, Timer } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ListRow, ListRowFooter, ListRowHeader } from "@/components/ui/list-row";
import { SpacezStayRequestActions } from "@/components/spacez-stay-request-actions";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { useNow } from "@/hooks/use-now";
import { api, ApiError } from "@/lib/api";
import { formatLeadTimestamp } from "@/lib/leads";
import { SLA_MS } from "@/lib/tickets";
import { cn, isBrowserTestIdentity } from "@/lib/utils";
import type { TicketKind, TicketOut } from "@/lib/types";

export const KIND_LABEL: Record<TicketKind, string> = {
  escalation: "Follow up",
  transfer: "Call back",
  technician: "Repair",
  photos: "Photos",
  whatsapp: "Message",
  promise: "Promise",
  missed_call: "Call back",
  payment: "Payment",
  request: "Guest request", // availability / price / payment-link asks Mira could not settle on the call
  stay_request: "Early / late", // Spacez only: early check-in / late check-out awaiting the team's yes or no
};

export const PHASE_LABEL = { in_stay: "Checked in", upcoming: "Upcoming guest", checked_out: "Checked out" } as const;

const URGENCY_TONE: Partial<Record<TicketOut["urgency"], StatusTone>> = {
  high: "orange",
  emergency: "destructive",
};

/** "12 min", "2 h 05 min", "1 d 3 h": a span in whole minutes, no seconds. */
export function formatSpan(ms: number): string {
  const minutes = Math.max(0, Math.floor(ms / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ${String(minutes % 60).padStart(2, "0")} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

/**
 * How long a ticket has been waiting: counts up while it is open (red once it is past what its urgency allows) and
 * stops at the time the host resolved it.
 */
export function ResolutionTimer({ ticket }: { ticket: TicketOut }) {
  const open = ticket.status === "open";
  const now = useNow(open);
  const started = new Date(ticket.created_at).getTime();
  if (!open) {
    const ended = ticket.resolved_at ? new Date(ticket.resolved_at).getTime() : started;
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground" title="Time it took to resolve">
        <Timer className="size-3.5" />
        {ticket.status === "dismissed" ? "Dismissed after" : "Resolved in"} {formatSpan(ended - started)}
      </span>
    );
  }
  const elapsed = now - started;
  const overdue = elapsed > SLA_MS;
  return (
    <span
      className={cn("inline-flex items-center gap-1 text-xs tabular-nums", overdue ? "font-medium text-destructive" : "text-muted-foreground")}
      title={overdue ? "Open longer than 24 hours: SLA breached" : "Time since this was raised"}
    >
      <Timer className="size-3.5" />
      Open for {formatSpan(elapsed)}
    </span>
  );
}

/**
 * One ticket: what the host has to do, when it was raised, how long it has waited, the property, and the actions.
 * Shown on the Tickets page (with the guest) and on a checked-in guest's own row (`showGuest` off: the row already
 * says who). It is the same ticket in both places, so resolving it in one resolves it in the other.
 */
export function TicketCard({
  ticket,
  showGuest = true,
  onChanged,
  onTranscript,
}: {
  ticket: TicketOut;
  showGuest?: boolean;
  onChanged: () => void;
  onTranscript: (ticket: TicketOut) => void;
}) {
  const [busy, setBusy] = useState(false);
  const phone = isBrowserTestIdentity(ticket.guest_phone) ? null : ticket.guest_phone;

  async function setStatus(status: "open" | "done" | "dismissed") {
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

  return (
    <ListRow variant="boxed" muted={ticket.status !== "open"}>
      <ListRowHeader>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {showGuest && ticket.booking_phase && (
            <StatusChip status={PHASE_LABEL[ticket.booking_phase]} tone={ticket.booking_phase === "in_stay" ? "live" : "neutral"} className="normal-case" />
          )}
          <StatusChip status={KIND_LABEL[ticket.kind]} tone="neutral" />
          {URGENCY_TONE[ticket.urgency] && <StatusChip status={ticket.urgency} tone={URGENCY_TONE[ticket.urgency]!} />}
          <span className="text-sm font-medium">{ticket.summary}</span>
        </div>
        <div className="flex flex-col items-end gap-0.5">
          <span className="whitespace-nowrap text-xs text-muted-foreground">{formatLeadTimestamp(ticket.created_at)}</span>
          <ResolutionTimer ticket={ticket} />
        </div>
      </ListRowHeader>
      {ticket.detail && <p className="text-sm leading-relaxed text-muted-foreground">{ticket.detail}</p>}
      <ListRowFooter className="flex-wrap justify-between pt-1">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {ticket.property_name && <Badge variant="outline">{ticket.property_name}</Badge>}
          {showGuest && (ticket.guest_name || phone) && <span>{[ticket.guest_name, phone].filter(Boolean).join(" · ")}</span>}
          {ticket.call_session_id && (
            <>
              <Button size="xs" variant="outline" onClick={() => onTranscript(ticket)}>
                <FileText className="size-3.5" />
                Transcript
              </Button>
              <Link href={`/dashboard/calls/${ticket.call_session_id}`} className="underline underline-offset-2 hover:text-foreground">
                View call
              </Link>
            </>
          )}
        </div>
        <div className="flex gap-2">
          {ticket.status === "open" && ticket.kind === "stay_request" ? (
            // Spacez only: answered with yes / no (which messages the guest), never "mark done".
            <SpacezStayRequestActions ticketId={ticket.id} onAnswered={onChanged} />
          ) : ticket.status === "open" ? (
            <>
              <Button size="sm" disabled={busy} onClick={() => setStatus("done")}>
                Mark done
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setStatus("dismissed")}>
                Dismiss
              </Button>
            </>
          ) : (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setStatus("open")}>
              Reopen
            </Button>
          )}
        </div>
      </ListRowFooter>
    </ListRow>
  );
}
