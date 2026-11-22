"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { BlockDatesPanel, type BlockRequest } from "@/components/calendar/block-dates-panel";
import { BookingDetailsPanel } from "@/components/calendar/booking-details-panel";
import { RightPanel } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { Hint } from "@/components/ui/sheet";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { bookingSourceColor, bookingsOnNight, daysInMonth, scopeMarkFor, toISODate } from "@/lib/calendar-grid";
import { cn } from "@/lib/utils";
import type { PropertyOut } from "@/lib/types";

const WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"]; // Monday first

/**
 * One property's own calendar, as a month: who is in on which night, blocked dates, and the iCal sync. Opened
 * from a property card or from the property's name in the Calendar tab. Click a booked day for its details,
 * a free day to block it.
 */
export function PropertyCalendarPanel({
  property,
  onOpenChange,
  onChanged,
}: {
  property: PropertyOut | null;
  onOpenChange: (open: boolean) => void;
  /** Bookings changed (so the page can refresh anything it shows about them). */
  onChanged?: () => void;
}) {
  const open = property !== null;
  const propertyId = property?.id ?? null;
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [selectedBookingId, setSelectedBookingId] = useState<string | null>(null);
  const [block, setBlock] = useState<BlockRequest>({ nonce: 0, open: false, propertyId: "", checkIn: "", checkOut: "" });
  const [syncing, setSyncing] = useState(false);

  // Fetched only while a property is open, so the Properties page itself stays as light as before.
  const { data: bookings, loading, refetch } = useAsync(
    () => (propertyId ? api.bookings.list() : Promise.resolve([])),
    [propertyId]
  );
  const { data: guests } = useAsync(() => (propertyId ? api.guests.list({}) : Promise.resolve([])), [propertyId]);

  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const numDays = daysInMonth(year, month);
  const monthLabel = cursor.toLocaleDateString("en-IN", { month: "long", year: "numeric" });
  const todayIso = toISODate(new Date());

  // Leading blanks so day 1 lands under its weekday (Monday first), then the days, then blanks to fill the last week.
  const cells = useMemo(() => {
    const lead = (new Date(year, month, 1).getDay() + 6) % 7;
    const out: (number | null)[] = [...Array(lead).fill(null), ...Array.from({ length: numDays }, (_, i) => i + 1)];
    while (out.length % 7 !== 0) out.push(null);
    return out;
  }, [year, month, numDays]);

  function refreshed() {
    refetch();
    onChanged?.();
  }

  function openBlock(day?: number) {
    if (!property) return;
    setBlock((current) => ({
      nonce: current.nonce + 1,
      open: true,
      propertyId: property.id,
      checkIn: day ? toISODate(new Date(year, month, day)) : "",
      checkOut: day ? toISODate(new Date(year, month, day + 1)) : "",
    }));
  }

  async function syncIcal() {
    if (!property) return;
    setSyncing(true);
    try {
      const result = await api.properties.syncIcal(property.id);
      toast.success(`iCal synced — ${result.created} created, ${result.updated} updated`);
      refreshed();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "iCal sync failed");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <>
      <RightPanel open={open} onOpenChange={onOpenChange} title={`${property?.name ?? ""} · Calendar`} size="xl">
        {property && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-1 rounded-lg border border-input p-0.5">
                <Button variant="ghost" size="sm" aria-label="Previous month" onClick={() => setCursor(new Date(year, month - 1, 1))}>
                  ←
                </Button>
                <span className="min-w-36 px-2 text-center text-sm font-semibold">{monthLabel}</span>
                <Button variant="ghost" size="sm" aria-label="Next month" onClick={() => setCursor(new Date(year, month + 1, 1))}>
                  →
                </Button>
              </div>
              <Button variant="cta" size="sm" onClick={() => openBlock()}>
                Block dates
              </Button>
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-3" style={{ background: bookingSourceColor.airbnb }} /> Airbnb
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-3" style={{ background: bookingSourceColor.manual }} /> Manual block
              </span>
              {property.room_rental_enabled && (
                <span title="E = the entire place is booked, 2R = 2 rooms are booked that night">
                  <span className="border border-border px-1 text-[11px] leading-4">E</span> entire place{" "}
                  <span className="ml-1 border border-border px-1 text-[11px] leading-4">2R</span> rooms
                </span>
              )}
            </div>

            {loading && !bookings ? (
              <Skeleton className="h-80 w-full" />
            ) : (
              <div className="border-t border-l border-border">
                <div className="grid grid-cols-7">
                  {WEEKDAYS.map((d, i) => (
                    <div
                      key={i}
                      className="border-r border-b border-border bg-card p-1.5 text-center text-xs font-bold tracking-[0.1em] text-muted-foreground uppercase"
                    >
                      {d}
                    </div>
                  ))}
                  {cells.map((day, i) => {
                    if (day === null) {
                      return <div key={`blank-${i}`} className="min-h-16 border-r border-b border-border bg-muted/40" />;
                    }
                    const iso = toISODate(new Date(year, month, day));
                    const booked = bookingsOnNight(bookings, property.id, iso);
                    const booking = booked[0];
                    const tone = booking ? (booking.platform === "airbnb" ? bookingSourceColor.airbnb : bookingSourceColor.manual) : undefined;
                    const needsConfirmation =
                      !!booking && (booking.needs_price_confirmation || booking.needs_attribution_review) && booking.check_in === iso;
                    const isToday = iso === todayIso;
                    return (
                      <button
                        key={day}
                        type="button"
                        title={
                          booking
                            ? `${booking.platform} — ${booking.guest_name ?? "no name"} (${booking.check_in} → ${booking.check_out}) — click for details`
                            : "Available — click to block"
                        }
                        onClick={() => (booking ? setSelectedBookingId(booking.id) : openBlock(day))}
                        style={tone ? { background: `color-mix(in srgb, ${tone} 70%, transparent)` } : undefined}
                        className={cn(
                          "relative flex min-h-16 flex-col items-start gap-0.5 border-r border-b border-border p-1.5 text-left transition-colors",
                          !booking && "hover:bg-accent",
                          isToday && "shadow-[inset_0_-3px_0_var(--primary)]"
                        )}
                      >
                        <span className={cn("text-[13px] tabular-nums", isToday ? "font-bold" : "text-muted-foreground")}>{day}</span>
                        {booking && (
                          <span className="w-full truncate text-[11px] leading-4 font-medium text-foreground">
                            {booking.guest_name ?? (booking.platform === "airbnb" ? "Airbnb" : "Blocked")}
                          </span>
                        )}
                        {property.room_rental_enabled && booking && (
                          <span className="absolute right-1 bottom-1 border border-border bg-card/80 px-1 text-[10px] leading-4 font-semibold">
                            {scopeMarkFor(booked)}
                          </span>
                        )}
                        {needsConfirmation && (
                          <span
                            aria-label="Needs confirmation"
                            className="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-(--status-pending) ring-1 ring-card"
                          />
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
              {property.ical_url ? (
                <>
                  <Hint>Bookings come in from this property&apos;s iCal link.</Hint>
                  <Button variant="outline" size="sm" onClick={syncIcal} disabled={syncing}>
                    {syncing ? "Syncing…" : "Sync iCal"}
                  </Button>
                </>
              ) : (
                <Hint>No iCal link, so only bookings you add here show up. Add one when you edit the property.</Hint>
              )}
            </div>
          </div>
        )}
      </RightPanel>

      <BlockDatesPanel
        request={block}
        properties={property ? [property] : []}
        guests={guests ?? []}
        lockProperty
        onOpenChange={(next) => setBlock((current) => ({ ...current, open: next }))}
        onCreated={refreshed}
      />
      <BookingDetailsPanel bookingId={selectedBookingId} onClose={() => setSelectedBookingId(null)} onChanged={refreshed} />
    </>
  );
}
