"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { StatusChip } from "@/components/status-chip";
import { TicketCard } from "@/components/ticket-card";
import { WhatsAppButton } from "@/components/whatsapp-button";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { formatINR } from "@/lib/format";
import { formatLeadTimestamp } from "@/lib/leads";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { ActiveBooking, ActiveBookingPayment, TicketOut } from "@/lib/types";

export function day(iso: string | null): string {
  if (!iso) return "dates not set";
  const [y, m, d] = iso.split("T")[0].split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

export function clock(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true }) : "";
}

/** The one line under the payment chip: what has been paid, what is still due, or what the guest has to do. */
export function paymentLine(payment: ActiveBookingPayment): string | null {
  if (payment.state === "paid" || payment.state === "part_paid") {
    const paid = `${formatINR(payment.amount_due)} paid`;
    const rest =
      payment.balance && payment.balance > 0
        ? `, ${formatINR(payment.balance)} due${payment.balance_due_on ? ` by ${day(payment.balance_due_on)}` : " before check-in"}`
        : "";
    return paid + rest + (payment.stay_code ? ` · stay code ${payment.stay_code}` : "");
  }
  if (payment.state === "link_sent" || payment.state === "payment_failed" || payment.state === "delivery_failed" || payment.state === "link_pending") {
    return `${formatINR(payment.amount_due)} to pay${payment.hold_expires_at ? ` · dates held until ${clock(payment.hold_expires_at)}` : ""}`;
  }
  if (payment.state === "expired") return `${formatINR(payment.amount_due)} link expired; the dates were released`;
  return null;
}

const PHASE_CHIP = {
  in_stay: { label: "Checked in", tone: "live" },
  upcoming: { label: "Upcoming", tone: "neutral" },
  checked_out: { label: "Checked out", tone: "neutral" },
} as const;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{value}</dd>
    </div>
  );
}

/**
 * The card that opens when the host clicks a guest in Leads -> Checked-in: the booking, the stay (with an early
 * check-in or late check-out they paid for), how they have paid, what they have asked for and every ticket raised
 * for them, open and earlier. It shows what the list shows plus the detail that would not fit on a row; resolving a
 * ticket or clearing a request here changes the list behind it (`onChanged`).
 */
export function GuestStayDialog({
  booking,
  onOpenChange,
  onChanged,
  onTranscript,
}: {
  booking: ActiveBooking | null;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
  onTranscript: (ticket: TicketOut) => void;
}) {
  const leadId = booking?.lead_id ?? null;
  const [busy, setBusy] = useState(false);
  // The tickets that are already done or dismissed: the list only carries the open ones.
  const { data: earlier } = useAsync(
    async () => (leadId ? (await api.tickets.list("resolved")).filter((t) => t.booking_lead_id === leadId) : []),
    // Read again when one of the guest's open tickets is resolved from this card, so it moves to "Earlier".
    [leadId, booking?.tickets.length]
  );

  async function markSeen() {
    if (!booking) return;
    setBusy(true);
    try {
      await api.activeBookings.markSeen(booking.lead_id);
      onChanged();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not clear the requests");
    } finally {
      setBusy(false);
    }
  }

  const phone = booking && !isBrowserTestIdentity(booking.phone) ? booking.phone : null;
  const phase = booking ? PHASE_CHIP[booking.phase] : null;
  const line = booking ? paymentLine(booking.payment) : null;
  const payment = booking?.payment;

  return (
    <Dialog open={booking !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        {booking && phase && payment && (
          <>
            <DialogHeader>
              <DialogTitle className="flex flex-wrap items-center gap-2 pr-8 text-lg">
                {booking.guest_name ?? "Unknown guest"}
                <StatusChip status={phase.label} tone={phase.tone} className="normal-case" />
                {booking.early_checkin && <StatusChip status="Early check-in" tone="progress" className="normal-case" />}
                {booking.late_checkout && <StatusChip status="Late check-out" tone="progress" className="normal-case" />}
              </DialogTitle>
              <DialogDescription>
                {[booking.property_name, `${day(booking.check_in)} → ${day(booking.check_out)}`].filter(Boolean).join(" · ")}
              </DialogDescription>
            </DialogHeader>

            <Section title="Booking">
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
                <Fact label="Property" value={booking.property_name} />
                <Fact label="Guests" value={booking.num_guests} />
                <Fact label="Nights" value={booking.nights} />
                <Fact label="Check-in" value={`${day(booking.check_in)}${booking.check_in_at ? `, ${clock(booking.check_in_at)}` : ""}`} />
                <Fact label="Check-out" value={`${day(booking.check_out)}${booking.check_out_at ? `, ${clock(booking.check_out_at)}` : ""}`} />
                <Fact label="Phone" value={phone} />
              </dl>
            </Section>

            <Section title="Stay">
              <p className="text-sm">
                {booking.phase === "in_stay" && "In the stay now."}
                {booking.phase === "upcoming" && `Arrives ${day(booking.check_in)}${booking.check_in_at ? ` from ${clock(booking.check_in_at)}` : ""}.`}
                {booking.phase === "checked_out" && "Has checked out; still has an open ticket."}
              </p>
              {(booking.early_checkin || booking.late_checkout) && (
                <p className="text-sm text-muted-foreground">
                  {booking.early_checkin && "Paid for an early check-in, so the stay starts before the property's usual time. "}
                  {booking.late_checkout && "Paid for a late check-out, so the stay runs past the property's usual time."}
                </p>
              )}
            </Section>

            <Section title="Payment">
              <div className="flex flex-wrap items-center gap-2">
                <StatusChip status={payment.label} tone={payment.tone} className="normal-case" />
                {payment.payment_down && <StatusChip status="Payment down" tone="destructive" className="normal-case" />}
              </div>
              {line && <p className="text-sm">{line}</p>}
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
                <Fact label="Total" value={payment.total_amount !== null ? formatINR(payment.total_amount) : null} />
                <Fact label="Paid on" value={payment.paid_at ? formatLeadTimestamp(payment.paid_at) : null} />
                <Fact label="Link sent" value={payment.link_sent_at ? formatLeadTimestamp(payment.link_sent_at) : null} />
              </dl>
              {payment.short_url && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    void navigator.clipboard?.writeText(payment.short_url!);
                    toast.success("Payment link copied");
                  }}
                >
                  Copy payment link
                </Button>
              )}
            </Section>

            <Section title={`Requests (${booking.activities.length})`}>
              {booking.activities.length === 0 ? (
                <p className="text-sm text-muted-foreground">Nothing new from this guest.</p>
              ) : (
                <>
                  <ul className="space-y-1.5 border-l-2 border-primary pl-3">
                    {booking.activities.map((activity) => (
                      <li key={activity.id} className="text-sm">
                        {activity.message}
                        <span className="ml-2 text-xs text-muted-foreground">{formatLeadTimestamp(activity.created_at)}</span>
                      </li>
                    ))}
                  </ul>
                  <Button size="sm" disabled={busy} onClick={markSeen}>
                    Mark seen
                  </Button>
                </>
              )}
            </Section>

            <Section title={`Open tickets (${booking.tickets.length})`}>
              {booking.tickets.length === 0 ? (
                <p className="text-sm text-muted-foreground">No open tickets.</p>
              ) : (
                <div>
                  {booking.tickets.map((ticket) => (
                    <TicketCard
                      key={ticket.id}
                      ticket={ticket}
                      showGuest={false}
                      onChanged={onChanged}
                      onTranscript={(t) => {
                        onOpenChange(false); // the transcript opens in a panel: the card makes way for it
                        onTranscript(t);
                      }}
                    />
                  ))}
                </div>
              )}
            </Section>

            {earlier && earlier.length > 0 && (
              <Section title={`Earlier tickets (${earlier.length})`}>
                <ul className="space-y-1.5">
                  {earlier.map((ticket) => (
                    <li key={ticket.id} className="flex flex-wrap items-center gap-2 text-sm">
                      <Badge variant="outline">{ticket.status === "dismissed" ? "Dismissed" : "Done"}</Badge>
                      <span>{ticket.summary}</span>
                      <span className="text-xs text-muted-foreground">{formatLeadTimestamp(ticket.created_at)}</span>
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            {phone && (
              <div className="flex justify-end">
                <WhatsAppButton phone={phone} size="sm" />
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
