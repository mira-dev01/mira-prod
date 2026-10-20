"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { BookingReconciliationPanel } from "@/components/booking-reconciliation";
import { GuestCombobox } from "@/components/guest-combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { BookingOut, PropertyOut } from "@/lib/types";

// Booking-source color mapping. Deliberately NOT drawn from the StatusTone
// system (live/pending/progress/destructive) -- those tones carry a
// semantic urgency/state meaning elsewhere in the app (a "live" call, a
// "pending" FAQ) that doesn't apply here: Airbnb-vs-manual is a category
// label, not a status. Reuses the existing chart-1..5 categorical palette
// instead, centralized here so the legend and grid cells share one lookup
// rather than each hardcoding var(--chart-n) separately.
const bookingSourceColor = {
  airbnb: "var(--chart-3)",
  manual: "var(--chart-4)",
} as const;

// Local calendar day -- toISOString() is UTC, which in IST turns a local-
// midnight cell date into the previous day (the block form was prefilled a
// day early).
function toISODate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

export default function CalendarPage() {
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const { data: properties, loading: loadingProperties } = useAsync(() => api.properties.list(), []);
  const { data: bookings, loading: loadingBookings, refetch } = useAsync(() => api.bookings.list(), []);
  // Unfiltered (no date range) so any past guest can be picked regardless of
  // when they last called -- this is a lookup list, not a report.
  const { data: guests } = useAsync(() => api.guests.list({}), []);

  const [blockOpen, setBlockOpen] = useState(false);
  const [blockPropertyId, setBlockPropertyId] = useState<string>("");
  const [blockCheckIn, setBlockCheckIn] = useState("");
  const [blockCheckOut, setBlockCheckOut] = useState("");
  const [blockGuestName, setBlockGuestName] = useState("");
  const [blockGuestPhone, setBlockGuestPhone] = useState<string | null>(null);
  const [blockPrice, setBlockPrice] = useState("");
  const [blockKind, setBlockKind] = useState<"reservation" | "blocked">("reservation");
  const [submitting, setSubmitting] = useState(false);

  // Clicking a booked cell opens the booking's details (price, "Mira
  // match?", unblock) -- see components/booking-reconciliation.tsx.
  const [selectedBookingId, setSelectedBookingId] = useState<string | null>(null);
  const [unblocking, setUnblocking] = useState(false);

  // Clicking a legend entry filters the grid down to just that source --
  // click again (or the same entry) to clear back to showing both. Lets a
  // host isolate e.g. just Airbnb bookings before going to update Airbnb's
  // own calendar, without hiding manual blocks from the grid permanently.
  const [sourceFilter, setSourceFilter] = useState<"airbnb" | "manual" | null>(null);

  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const numDays = daysInMonth(year, month);
  const days = useMemo(() => Array.from({ length: numDays }, (_, i) => i + 1), [numDays]);

  const monthLabel = cursor.toLocaleDateString("en-IN", { month: "long", year: "numeric" });

  // Compare as YYYY-MM-DD strings, NOT Date objects. `new Date("2026-08-01")`
  // parses as UTC midnight, while `new Date(year, month, day)` is local
  // midnight -- in IST (UTC+5:30) those differ, so a booking's checkout day
  // (Aug 1, meant to be free) would flip to blocked because the local-
  // constructed cell Date landed before the UTC-parsed checkOut Date.
  // Confirmed live: Aug 1/11/17/23 for Olive all showed blocked despite
  // being the checkout days of preceding bookings. String comparison
  // sidesteps timezones entirely since check_in/check_out come from the
  // backend as plain YYYY-MM-DD.
  function allBookingsForDay(propertyId: string, day: number): BookingOut[] {
    if (!bookings) return [];
    const mm = String(month + 1).padStart(2, "0");
    const dd = String(day).padStart(2, "0");
    const cellIso = `${year}-${mm}-${dd}`;
    return bookings.filter((b) => {
      if (b.property_id !== propertyId || b.status !== "confirmed") return false;
      return cellIso >= b.check_in && cellIso < b.check_out;
    });
  }

  // sourceFilter only changes what's *shown* -- click/block eligibility
  // below still checks allBookingsForDay so a booking hidden by the filter
  // can't be mistaken for a free day and get double-booked.
  function bookingsForDay(propertyId: string, day: number): BookingOut[] {
    return allBookingsForDay(propertyId, day).filter((b) => !sourceFilter || b.platform === sourceFilter);
  }

  function toggleSourceFilter(source: "airbnb" | "manual") {
    setSourceFilter((current) => (current === source ? null : source));
  }

  function openBlockDialog(propertyId?: string, day?: number) {
    setBlockPropertyId(propertyId ?? properties?.[0]?.id ?? "");
    if (day) {
      const d = new Date(year, month, day);
      setBlockCheckIn(toISODate(d));
      const checkout = new Date(year, month, day + 1);
      setBlockCheckOut(toISODate(checkout));
    } else {
      setBlockCheckIn("");
      setBlockCheckOut("");
    }
    setBlockGuestName("");
    setBlockGuestPhone(null);
    setBlockPrice("");
    setBlockKind("reservation");
    setBlockOpen(true);
  }

  async function handleBlockDates(e: React.FormEvent) {
    e.preventDefault();
    if (!blockPropertyId || !blockCheckIn || !blockCheckOut) return;
    setSubmitting(true);
    try {
      await api.bookings.create({
        property_id: blockPropertyId,
        check_in: blockCheckIn,
        check_out: blockCheckOut,
        guest_name: blockGuestName || null,
        guest_phone: blockGuestPhone,
        platform: "manual",
        kind: blockKind,
        final_booking_price: blockKind === "reservation" && blockPrice ? Number(blockPrice) : null,
      });
      toast.success("Dates blocked");
      setBlockOpen(false);
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to block dates");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleUnblock() {
    if (!selectedBookingId) return;
    setUnblocking(true);
    try {
      await api.bookings.cancel(selectedBookingId);
      toast.success("Dates unblocked");
      setSelectedBookingId(null);
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to unblock dates");
    } finally {
      setUnblocking(false);
    }
  }

  const loading = loadingProperties || loadingBookings;
  const needsConfirmationCount = (bookings ?? []).filter(
    (b) => b.needs_price_confirmation || b.needs_attribution_review
  ).length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Calendar</h1>
          <p className="text-sm text-muted-foreground">
            Master availability across your portfolio. Airbnb bookings sync in automatically every 15
            minutes (or use &quot;Sync iCal&quot; on a property); block dates here once you confirm a
            booking yourself.
          </p>
        </div>
        <Button onClick={() => openBlockDialog()}>Block dates</Button>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <Button variant="outline" size="sm" onClick={() => setCursor(new Date(year, month - 1, 1))}>
          ←
        </Button>
        <span className="text-sm font-medium">{monthLabel}</span>
        <Button variant="outline" size="sm" onClick={() => setCursor(new Date(year, month + 1, 1))}>
          →
        </Button>
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <button
            type="button"
            onClick={() => toggleSourceFilter("airbnb")}
            title="Filter the grid to only Airbnb bookings — handy before updating Airbnb's own calendar"
            className={cn(
              "flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-colors hover:bg-accent",
              sourceFilter === "airbnb" && "bg-accent font-medium text-foreground",
              sourceFilter === "manual" && "opacity-50"
            )}
          >
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: bookingSourceColor.airbnb }} />
            Airbnb
          </button>
          <button
            type="button"
            onClick={() => toggleSourceFilter("manual")}
            title="Filter the grid to only manual blocks — handy before updating Airbnb's own calendar"
            className={cn(
              "flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-colors hover:bg-accent",
              sourceFilter === "manual" && "bg-accent font-medium text-foreground",
              sourceFilter === "airbnb" && "opacity-50"
            )}
          >
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: bookingSourceColor.manual }} />
            Manual block
          </button>
          {needsConfirmationCount > 0 && (
            <span
              className="flex items-center gap-1"
              title="Bookings with a missing final price or a possible Mira match -- click one to confirm"
            >
              <span className="inline-block size-1.5 rounded-full bg-(--status-pending)" />
              {needsConfirmationCount} need{needsConfirmationCount === 1 ? "s" : ""} confirmation
            </span>
          )}
          {sourceFilter && (
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => setSourceFilter(null)}>
              Clear filter
            </Button>
          )}
        </div>
      </div>

      {loading ? (
        <Skeleton className="h-96 w-full" />
      ) : !properties || properties.length === 0 ? (
        <p className="text-sm text-muted-foreground">No properties yet — add one to see its calendar here.</p>
      ) : (
        <div className="max-h-[calc(100vh-7rem)] w-full overflow-auto rounded-lg border">
          {/* table-layout: fixed + w-full ties the table's rendered width to
              this box explicitly -- an auto-layout table has no contract
              with its container's width, which is what produced the
              "squished" look as the surrounding flex box resized (see
              restructure.md Phase 5). The property column gets a fixed
              width; day columns split the remainder evenly via inline
              style since their count is dynamic (28-31, can't be a static
              Tailwind class). */}
          <table className="w-full border-collapse text-sm" style={{ tableLayout: "fixed" }}>
            <colgroup>
              <col style={{ width: "140px" }} />
              {days.map((day) => (
                <col key={day} style={{ width: `calc((100% - 140px) / ${numDays})` }} />
              ))}
            </colgroup>
            <thead>
              <tr>
                <th className="sticky top-0 left-0 z-20 border-b bg-card p-2 text-left">Property</th>
                {days.map((day) => (
                  <th
                    key={day}
                    className="sticky top-0 z-10 border-b border-l bg-card p-1 text-center text-xs font-normal text-muted-foreground"
                  >
                    {day}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {properties.map((property: PropertyOut) => (
                <tr key={property.id}>
                  <td className="sticky left-0 z-10 truncate border-b bg-card p-2 font-medium">
                    {property.name}
                  </td>
                  {days.map((day) => {
                    // Visible (filtered) booking drives color; the real,
                    // unfiltered occupancy drives click behavior so a
                    // booking hidden by sourceFilter can't be mistaken for a
                    // free day and get double-booked.
                    const visibleBooking = bookingsForDay(property.id, day)[0];
                    const actualBooking = allBookingsForDay(property.id, day)[0];
                    const tone = visibleBooking
                      ? visibleBooking.platform === "airbnb"
                        ? bookingSourceColor.airbnb
                        : bookingSourceColor.manual
                      : undefined;
                    const hiddenByFilter = !visibleBooking && !!actualBooking;
                    // One small marker on the stay's first visible day, not
                    // every night of it -- keeps the grid uncluttered.
                    const mm = String(month + 1).padStart(2, "0");
                    const cellIso = `${year}-${mm}-${String(day).padStart(2, "0")}`;
                    const showMarker =
                      !!visibleBooking &&
                      (visibleBooking.needs_price_confirmation || visibleBooking.needs_attribution_review) &&
                      (visibleBooking.check_in === cellIso || (day === 1 && visibleBooking.check_in < cellIso));
                    return (
                      <td
                        key={day}
                        title={
                          visibleBooking
                            ? `${visibleBooking.platform} -- ${visibleBooking.guest_name ?? "no name"} (${visibleBooking.check_in} → ${visibleBooking.check_out})${showMarker ? " -- needs confirmation" : ""} -- click for details`
                            : hiddenByFilter
                              ? "Booking hidden by the source filter above"
                              : "Available -- click to block"
                        }
                        onClick={() => {
                          if (visibleBooking) setSelectedBookingId(visibleBooking.id);
                          else if (!hiddenByFilter) openBlockDialog(property.id, day);
                        }}
                        style={tone ? { background: `color-mix(in srgb, ${tone} 70%, transparent)` } : undefined}
                        onMouseEnter={(e) => {
                          if (tone) e.currentTarget.style.background = tone;
                        }}
                        onMouseLeave={(e) => {
                          if (tone) e.currentTarget.style.background = `color-mix(in srgb, ${tone} 70%, transparent)`;
                        }}
                        className={cn(
                          "relative h-7 border-b border-l",
                          hiddenByFilter ? "cursor-not-allowed opacity-30" : "cursor-pointer",
                          !visibleBooking && !hiddenByFilter && "hover:bg-accent"
                        )}
                      >
                        {showMarker && (
                          <span
                            aria-label="Needs confirmation"
                            className="absolute top-1 right-1 size-1.5 rounded-full bg-(--status-pending) ring-1 ring-card"
                          />
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <RightPanel
        open={blockOpen}
        onOpenChange={setBlockOpen}
        title="Block dates"
        footer={
          <RightPanelFooterButton type="submit" form="block-dates-form" disabled={submitting}>
            {submitting ? "Blocking…" : "Block dates"}
          </RightPanelFooterButton>
        }
      >
        <form id="block-dates-form" onSubmit={handleBlockDates} className="space-y-4">
          <div className="space-y-2">
            <Label>Property</Label>
            <Select value={blockPropertyId} onValueChange={(v) => v && setBlockPropertyId(v)}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select a property">
                  {(value: string) => properties?.find((p) => p.id === value)?.name ?? "Select a property"}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {properties?.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="block-check-in">Check-in</Label>
              <Input
                id="block-check-in"
                type="date"
                required
                value={blockCheckIn}
                onChange={(e) => setBlockCheckIn(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="block-check-out">Check-out</Label>
              <Input
                id="block-check-out"
                type="date"
                required
                value={blockCheckOut}
                onChange={(e) => setBlockCheckOut(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="block-guest-name">Guest name (optional)</Label>
            <GuestCombobox
              guests={guests ?? []}
              name={blockGuestName}
              onNameChange={setBlockGuestName}
              onSelectGuest={(guest) => setBlockGuestPhone(guest?.phone ?? null)}
            />
            {blockGuestPhone && (
              <p className="text-xs text-muted-foreground">
                Linked to existing guest ({blockGuestPhone}) — this stay will show up on their guest
                profile.
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label>What are these dates for?</Label>
            <Select value={blockKind} onValueChange={(v) => v && setBlockKind(v as "reservation" | "blocked")}>
              <SelectTrigger className="w-full">
                <SelectValue>
                  {(value: string) => (value === "blocked" ? "Blocked — no guest (owner use, maintenance)" : "A guest booking")}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="reservation">A guest booking</SelectItem>
                <SelectItem value="blocked">Blocked — no guest (owner use, maintenance)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {blockKind === "reservation" && (
          <div className="space-y-2">
            <Label htmlFor="block-price">Final booking price (optional, total stay ₹)</Label>
            <Input
              id="block-price"
              type="number"
              inputMode="numeric"
              min={1}
              value={blockPrice}
              onChange={(e) => setBlockPrice(e.target.value)}
              placeholder="Leave empty if not decided yet"
            />
          </div>
          )}
        </form>
      </RightPanel>

      <BookingReconciliationPanel
        bookingId={selectedBookingId}
        onOpenChange={(open) => !open && setSelectedBookingId(null)}
        onChanged={refetch}
        footer={
          <RightPanelFooterButton variant="destructive" onClick={handleUnblock} disabled={unblocking}>
            {unblocking ? "Unblocking…" : "Unblock these dates"}
          </RightPanelFooterButton>
        }
      >
        <p className="border-t pt-4 text-xs text-muted-foreground">
          Unblock for a cancellation or dates blocked by mistake. If this came from Airbnb and is still active
          there, it will reappear on the next iCal sync.
        </p>
      </BookingReconciliationPanel>
    </div>
  );
}
