"use client";

import { useEffect, useRef, useState } from "react";
import { Bell, BellOff, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { ActivityBadge } from "@/components/activity-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ListRow, ListRowFooter, ListRowHeader } from "@/components/ui/list-row";
import { Skeleton } from "@/components/ui/skeleton";
import { Cell, CellTitle, Sheet, Toolbar } from "@/components/ui/sheet";
import { StatusChip } from "@/components/status-chip";
import { GuestStayDialog, clock, day, paymentLine } from "@/components/guest-stay-dialog";
import { TicketCard } from "@/components/ticket-card";
import { TicketTranscriptPanel } from "@/components/ticket-transcript-panel";
import { WhatsAppButton } from "@/components/whatsapp-button";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { useBookingActivity } from "@/lib/booking-activity-context";
import { formatLeadTimestamp } from "@/lib/leads";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { ActiveBooking, TicketOut } from "@/lib/types";

const WHEN_LABEL = { confirmed: "Booked when confirmed", paid: "Booked when paid", link_sent: "Booked when the link is sent" };

function GuestRow({
  booking,
  onSeen,
  onTicketChanged,
  onTranscript,
  onOpen,
}: {
  booking: ActiveBooking;
  onSeen: () => void;
  onTicketChanged: () => void;
  onTranscript: (ticket: TicketOut) => void;
  onOpen: (booking: ActiveBooking) => void;
}) {
  const [busy, setBusy] = useState(false);
  const phone = isBrowserTestIdentity(booking.phone) ? null : booking.phone;
  const line = paymentLine(booking.payment);

  async function markSeen() {
    setBusy(true);
    try {
      await api.activeBookings.markSeen(booking.lead_id);
      onSeen();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not clear the requests");
    } finally {
      setBusy(false);
    }
  }

  return (
    // The whole row opens the guest's card; the buttons and tickets inside it keep their own clicks.
    <ListRow variant="boxed" interactive onClick={() => onOpen(booking)}>
      <ListRowHeader>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <button
            type="button"
            className="text-sm font-medium underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none"
            onClick={(e) => {
              e.stopPropagation();
              onOpen(booking);
            }}
          >
            {booking.guest_name ?? "Unknown guest"}
          </button>
          <StatusChip status={booking.payment.label} tone={booking.payment.tone} className="normal-case" />
          {booking.payment.payment_down && <StatusChip status="Payment down" tone="destructive" className="normal-case" />}
          {booking.phase === "checked_out" && <StatusChip status="Checked out" tone="neutral" className="normal-case" />}
          {booking.early_checkin && <StatusChip status="Early check-in" tone="progress" className="normal-case" />}
          {booking.late_checkout && <StatusChip status="Late check-out" tone="progress" className="normal-case" />}
          <ActivityBadge count={booking.activity_count + booking.ticket_count} noun="item" />
        </div>
        <span className="text-sm whitespace-nowrap">
          {day(booking.check_in)} → {day(booking.check_out)}
        </span>
      </ListRowHeader>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
        {booking.property_name && <Badge variant="outline">{booking.property_name}</Badge>}
        {booking.nights !== null && (
          <span>
            {booking.nights} night{booking.nights === 1 ? "" : "s"}
          </span>
        )}
        {booking.num_guests !== null && <span>{booking.num_guests} guests</span>}
        {booking.phase === "in_stay" && booking.check_out_at && <span>checks out {clock(booking.check_out_at)} on {day(booking.check_out)}</span>}
        {booking.phase === "in_stay" && booking.early_checkin && booking.check_in_at && <span>checked in {clock(booking.check_in_at)}</span>}
        {booking.phase === "upcoming" && booking.check_in_at && <span>checks in {clock(booking.check_in_at)}</span>}
        {phone && <span>{phone}</span>}
      </div>

      {line && <p className="text-sm">{line}</p>}

      {booking.activities.length > 0 && (
        <ul className="space-y-1.5 border-l-2 border-primary pl-3">
          {booking.activities.map((activity) => (
            <li key={activity.id} className="text-sm">
              {activity.message}
              <span className="ml-2 text-xs text-muted-foreground">{formatLeadTimestamp(activity.created_at)}</span>
            </li>
          ))}
        </ul>
      )}

      {booking.tickets.length > 0 && (
        <div className="cursor-default rounded-md border border-border px-3 pt-3" onClick={(e) => e.stopPropagation()}>
          <p className="pb-1 text-xs font-medium text-muted-foreground">
            Open ticket{booking.tickets.length === 1 ? "" : "s"} ({booking.tickets.length})
          </p>
          {booking.tickets.map((ticket) => (
            <TicketCard key={ticket.id} ticket={ticket} showGuest={false} onChanged={onTicketChanged} onTranscript={onTranscript} />
          ))}
        </div>
      )}

      <ListRowFooter className="flex-wrap justify-end gap-2 pt-1" onClick={(e) => e.stopPropagation()}>
        {booking.payment.short_url && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              void navigator.clipboard?.writeText(booking.payment.short_url!);
              toast.success("Payment link copied");
            }}
          >
            Copy payment link
          </Button>
        )}
        {booking.activity_count > 0 && (
          <Button size="sm" disabled={busy} onClick={markSeen}>
            Mark seen
          </Button>
        )}
        <WhatsAppButton phone={phone} />
      </ListRowFooter>
    </ListRow>
  );
}

function Section({
  title,
  bookings,
  empty,
  onSeen,
  onTicketChanged,
  onTranscript,
  onOpen,
}: {
  title: string;
  bookings: ActiveBooking[];
  empty: string;
  onSeen: () => void;
  onTicketChanged: () => void;
  onTranscript: (ticket: TicketOut) => void;
  onOpen: (booking: ActiveBooking) => void;
}) {
  return (
    <Cell>
      <CellTitle>
        {title} ({bookings.length})
      </CellTitle>
      {bookings.length === 0 ? (
        <p className="text-sm text-muted-foreground">{empty}</p>
      ) : (
        <div>
          {bookings.map((booking) => (
            <GuestRow key={booking.lead_id} booking={booking} onSeen={onSeen} onTicketChanged={onTicketChanged} onTranscript={onTranscript} onOpen={onOpen} />
          ))}
        </div>
      )}
    </Cell>
  );
}

/**
 * Leads -> Checked-in: the booked guests who are in their stay (the ones leaving soonest first), then the upcoming
 * ones soonest first, each with how they have paid, any requests they have made and the tickets raised for them. A
 * guest is checked in from the property's own check-in time (or earlier, if they paid for an early check-in) until
 * its check-out time (or later, for a paid late check-out); one who has left but still has an open ticket stays,
 * marked "Checked out", until it is done. A guest is booked by the host's own rule (Agent -> Payments), and the same
 * guest shows as closed in Active leads: it is the same lead.
 */
export function CheckedInTab() {
  const { data, loading, refetch } = useAsync(() => api.activeBookings.list(), []);
  const { version, refresh, soundOn, setSoundOn } = useBookingActivity();
  const [viewing, setViewing] = useState<TicketOut | null>(null);
  // The guest whose card is open, by lead id so the card follows the list when it is read again.
  const [openId, setOpenId] = useState<string | null>(null);
  const opened = data ? [...data.in_stay, ...data.upcoming].find((b) => b.lead_id === openId) ?? null : null;

  // A new request or ticket elsewhere in the dashboard changes the numbers: read the list again when it does.
  const lastVersion = useRef(version);
  useEffect(() => {
    if (lastVersion.current === version) return;
    lastVersion.current = version;
    void refetch();
  }, [version, refetch]);

  function seen() {
    void refresh();
    void refetch();
  }

  return (
    <>
      <Sheet>
        <Toolbar className="justify-between">
          <div className="flex flex-wrap items-center gap-2">
            {data && <span className="text-sm text-muted-foreground">{WHEN_LABEL[data.active_when]}</span>}
            {data?.payment_down && <StatusChip status="Payment down: payments cannot be checked right now" tone="destructive" className="normal-case" />}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="icon-sm"
              variant="outline"
              aria-label={soundOn ? "Turn the new-request sound off" : "Turn the new-request sound on"}
              title={soundOn ? "Sound on" : "Sound off"}
              aria-pressed={soundOn}
              onClick={() => setSoundOn(!soundOn)}
            >
              {soundOn ? <Bell className="size-4" /> : <BellOff className="size-4" />}
            </Button>
            <Button size="icon-sm" variant="outline" aria-label="Refresh" title="Refresh" onClick={seen}>
              <RefreshCw className="size-4" />
            </Button>
          </div>
        </Toolbar>

        {loading && !data ? (
          <Cell>
            <Skeleton className="h-48 w-full" />
          </Cell>
        ) : data ? (
          <>
            <Section title="Checked in" bookings={data.in_stay} empty="No guest is checked in right now." onSeen={seen} onTicketChanged={seen} onTranscript={setViewing} onOpen={(b) => setOpenId(b.lead_id)} />
            <Section title="Upcoming" bookings={data.upcoming} empty="No upcoming bookings yet." onSeen={seen} onTicketChanged={seen} onTranscript={setViewing} onOpen={(b) => setOpenId(b.lead_id)} />
          </>
        ) : (
          <Cell>
            <p className="text-sm text-muted-foreground">Could not load the bookings.</p>
          </Cell>
        )}
      </Sheet>

      <GuestStayDialog
        booking={opened}
        onOpenChange={(open) => !open && setOpenId(null)}
        onChanged={seen}
        onTranscript={setViewing}
      />

      <TicketTranscriptPanel
        callSessionId={viewing?.call_session_id ?? null}
        guestName={viewing?.guest_name ?? null}
        onOpenChange={(open) => !open && setViewing(null)}
      />
    </>
  );
}
