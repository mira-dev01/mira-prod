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
import { WhatsAppButton } from "@/components/whatsapp-button";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { useBookingActivity } from "@/lib/booking-activity-context";
import { formatINR } from "@/lib/format";
import { formatLeadTimestamp } from "@/lib/leads";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { ActiveBooking, ActiveBookingPayment } from "@/lib/types";

const WHEN_LABEL = { confirmed: "Booked when confirmed", paid: "Booked when paid", link_sent: "Booked when the link is sent" };

function day(iso: string | null): string {
  if (!iso) return "dates not set";
  const [y, m, d] = iso.split("T")[0].split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function clock(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true }) : "";
}

/** The one line under the payment chip: what has been paid, what is still due, or what the guest has to do. */
function paymentLine(payment: ActiveBookingPayment): string | null {
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

function GuestRow({ booking, onSeen }: { booking: ActiveBooking; onSeen: () => void }) {
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
    <ListRow variant="boxed">
      <ListRowHeader>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{booking.guest_name ?? "Unknown guest"}</span>
          <StatusChip status={booking.payment.label} tone={booking.payment.tone} className="normal-case" />
          {booking.payment.payment_down && <StatusChip status="Payment down" tone="destructive" className="normal-case" />}
          {booking.activity_count > 0 && <ActivityBadge count={booking.activity_count} />}
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

      <ListRowFooter className="flex-wrap justify-end gap-2 pt-1">
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

function Section({ title, bookings, empty, onSeen }: { title: string; bookings: ActiveBooking[]; empty: string; onSeen: () => void }) {
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
            <GuestRow key={booking.lead_id} booking={booking} onSeen={onSeen} />
          ))}
        </div>
      )}
    </Cell>
  );
}

/**
 * Leads -> Active bookings: the booked guests, the ones in their stay first, then the upcoming ones soonest first,
 * each with how they have paid and any requests they have made. A guest is booked by the host's own rule (Agent ->
 * Payments), and the same guest shows as closed in Booking requests: it is the same lead.
 */
export function ActiveBookingsTab() {
  const { data, loading, refetch } = useAsync(() => api.activeBookings.list(), []);
  const { count, refresh, soundOn, setSoundOn } = useBookingActivity();

  // A new request elsewhere in the dashboard changes the number here: re-read the list when it does.
  const lastCount = useRef(count);
  useEffect(() => {
    if (lastCount.current === count) return;
    lastCount.current = count;
    void refetch();
  }, [count, refetch]);

  function seen() {
    void refresh();
    void refetch();
  }

  return (
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
          <Button size="icon-sm" variant="outline" aria-label="Refresh" title="Refresh" onClick={() => void refetch()}>
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
          <Section title="In stay" bookings={data.in_stay} empty="No guest is staying right now." onSeen={seen} />
          <Section title="Upcoming" bookings={data.upcoming} empty="No upcoming bookings yet." onSeen={seen} />
        </>
      ) : (
        <Cell>
          <p className="text-sm text-muted-foreground">Could not load the bookings.</p>
        </Cell>
      )}
    </Sheet>
  );
}
