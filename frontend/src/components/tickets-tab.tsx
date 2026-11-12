"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ListRow, ListRowFooter, ListRowHeader } from "@/components/ui/list-row";
import { Skeleton } from "@/components/ui/skeleton";
import { Cell, Sheet, Toolbar } from "@/components/ui/sheet";
import { SegmentedChoice } from "@/components/segmented-choice";
import { SpacezStayRequestActions } from "@/components/spacez-stay-request-actions";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { formatLeadTimestamp } from "@/lib/leads";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { TicketKind, TicketOut } from "@/lib/types";

const KIND_LABEL: Record<TicketKind, string> = {
  escalation: "Follow up",
  transfer: "Call back",
  technician: "Repair",
  photos: "Photos",
  whatsapp: "Message",
  promise: "Promise",
  missed_call: "Call back",
  payment: "Payment",
  stay_request: "Early / late", // Spacez only: early check-in / late check-out awaiting the team's yes or no
};

const URGENCY_TONE: Partial<Record<TicketOut["urgency"], StatusTone>> = {
  high: "orange",
  emergency: "destructive",
};

/**
 * Leads → "Tickets": everything Mira told a guest would happen after the call,
 * until the host marks it done. Nothing here is new data to look after: it is
 * the list behind each "I've flagged this to the host".
 */
export function TicketsTab({ onChanged }: { onChanged?: () => void }) {
  const [view, setView] = useState<"open" | "resolved">("open");
  const { data: tickets, loading, refetch } = useAsync(() => api.tickets.list(view), [view]);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function setStatus(ticket: TicketOut, status: "open" | "done" | "dismissed") {
    setBusyId(ticket.id);
    try {
      await api.tickets.setStatus(ticket.id, status);
      refetch();
      onChanged?.();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not update the ticket");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Sheet>
      <Toolbar className="justify-between">
        <p className="text-sm text-muted-foreground">Promised to guests, until you mark it done.</p>
        <SegmentedChoice
          label="Show tickets"
          value={view}
          onChange={setView}
          options={[
            { value: "open", label: "Open" },
            { value: "resolved", label: "Done" },
          ]}
        />
      </Toolbar>

      <Cell>
        {loading ? (
          <Skeleton className="h-48 w-full" />
        ) : !tickets || tickets.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {view === "open" ? "Nothing waiting on you." : "Nothing marked done yet."}
          </p>
        ) : (
          <div>
            {tickets.map((ticket) => {
              const phone = isBrowserTestIdentity(ticket.guest_phone) ? null : ticket.guest_phone;
              return (
                <ListRow key={ticket.id} variant="boxed" muted={ticket.status !== "open"}>
                  <ListRowHeader>
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <StatusChip status={KIND_LABEL[ticket.kind]} tone="neutral" />
                      {URGENCY_TONE[ticket.urgency] && (
                        <StatusChip status={ticket.urgency} tone={URGENCY_TONE[ticket.urgency]!} />
                      )}
                      <span className="text-sm font-medium">{ticket.summary}</span>
                    </div>
                    <span className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatLeadTimestamp(ticket.created_at)}
                    </span>
                  </ListRowHeader>
                  {ticket.detail && <p className="text-sm leading-relaxed text-muted-foreground">{ticket.detail}</p>}
                  <ListRowFooter className="flex-wrap justify-between pt-1">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      {ticket.property_name && <Badge variant="outline">{ticket.property_name}</Badge>}
                      {(ticket.guest_name || phone) && (
                        <span>{[ticket.guest_name, phone].filter(Boolean).join(" · ")}</span>
                      )}
                      {ticket.call_session_id && (
                        <Link
                          href={`/dashboard/calls/${ticket.call_session_id}`}
                          className="underline underline-offset-2 hover:text-foreground"
                        >
                          View call
                        </Link>
                      )}
                    </div>
                    <div className="flex gap-2">
                      {ticket.status === "open" && ticket.kind === "stay_request" ? (
                        // Spacez only: answered with yes / no (which messages the guest), never "mark done".
                        <SpacezStayRequestActions
                          ticketId={ticket.id}
                          onAnswered={() => {
                            refetch();
                            onChanged?.();
                          }}
                        />
                      ) : ticket.status === "open" ? (
                        <>
                          <Button size="sm" disabled={busyId === ticket.id} onClick={() => setStatus(ticket, "done")}>
                            Mark done
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busyId === ticket.id}
                            onClick={() => setStatus(ticket, "dismissed")}
                          >
                            Dismiss
                          </Button>
                        </>
                      ) : (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busyId === ticket.id}
                          onClick={() => setStatus(ticket, "open")}
                        >
                          Reopen
                        </Button>
                      )}
                    </div>
                  </ListRowFooter>
                </ListRow>
              );
            })}
          </div>
        )}
      </Cell>
    </Sheet>
  );
}
