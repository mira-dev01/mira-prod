"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
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

function toISODate(d: Date): string {
  return d.toISOString().slice(0, 10);
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
  const [submitting, setSubmitting] = useState(false);

  const [unblockTarget, setUnblockTarget] = useState<{ booking: BookingOut; propertyName: string } | null>(null);
  const [unblocking, setUnblocking] = useState(false);

  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const numDays = daysInMonth(year, month);
  const days = useMemo(() => Array.from({ length: numDays }, (_, i) => i + 1), [numDays]);

  const monthLabel = cursor.toLocaleDateString("en-IN", { month: "long", year: "numeric" });

  function bookingsForDay(propertyId: string, day: number): BookingOut[] {
    if (!bookings) return [];
    const cellDate = new Date(year, month, day);
    return bookings.filter((b) => {
      if (b.property_id !== propertyId || b.status !== "confirmed") return false;
      const checkIn = new Date(b.check_in);
      const checkOut = new Date(b.check_out);
      return cellDate >= checkIn && cellDate < checkOut;
    });
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
    if (!unblockTarget) return;
    setUnblocking(true);
    try {
      await api.bookings.cancel(unblockTarget.booking.id);
      toast.success("Dates unblocked");
      setUnblockTarget(null);
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to unblock dates");
    } finally {
      setUnblocking(false);
    }
  }

  const loading = loadingProperties || loadingBookings;

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
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: bookingSourceColor.airbnb }} />
            Airbnb
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: bookingSourceColor.manual }} />
            Manual block
          </span>
        </div>
      </div>

      {loading ? (
        <Skeleton className="h-96 w-full" />
      ) : !properties || properties.length === 0 ? (
        <p className="text-sm text-muted-foreground">No properties yet — add one to see its calendar here.</p>
      ) : (
        <div className="max-h-[calc(100vh-20rem)] w-full overflow-auto rounded-lg border">
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
                    const dayBookings = bookingsForDay(property.id, day);
                    const booking = dayBookings[0];
                    const tone = booking
                      ? booking.platform === "airbnb"
                        ? bookingSourceColor.airbnb
                        : bookingSourceColor.manual
                      : undefined;
                    return (
                      <td
                        key={day}
                        title={
                          booking
                            ? `${booking.platform} -- ${booking.guest_name ?? "no name"} (${booking.check_in} → ${booking.check_out}) -- click to unblock`
                            : "Available -- click to block"
                        }
                        onClick={() =>
                          booking
                            ? setUnblockTarget({ booking, propertyName: property.name })
                            : openBlockDialog(property.id, day)
                        }
                        style={tone ? { background: `color-mix(in srgb, ${tone} 70%, transparent)` } : undefined}
                        onMouseEnter={(e) => {
                          if (tone) e.currentTarget.style.background = tone;
                        }}
                        onMouseLeave={(e) => {
                          if (tone) e.currentTarget.style.background = `color-mix(in srgb, ${tone} 70%, transparent)`;
                        }}
                        className={cn("h-7 cursor-pointer border-b border-l", !booking && "hover:bg-accent")}
                      />
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
        </form>
      </RightPanel>

      <RightPanel
        open={!!unblockTarget}
        onOpenChange={(open) => !open && setUnblockTarget(null)}
        title="Unblock dates"
        footer={
          <RightPanelFooterButton variant="destructive" onClick={handleUnblock} disabled={unblocking}>
            {unblocking ? "Unblocking…" : "Unblock these dates"}
          </RightPanelFooterButton>
        }
      >
        {unblockTarget && (
          <div className="space-y-4">
            <div className="space-y-1 rounded-md border p-3 text-sm">
              <p className="font-medium">{unblockTarget.propertyName}</p>
              <p className="text-muted-foreground">
                {unblockTarget.booking.check_in} → {unblockTarget.booking.check_out}
              </p>
              <p className="text-muted-foreground capitalize">
                {unblockTarget.booking.platform}
                {unblockTarget.booking.guest_name ? ` · ${unblockTarget.booking.guest_name}` : ""}
              </p>
            </div>
            <p className="text-sm text-muted-foreground">
              Use this for a cancellation or if these dates were blocked by mistake. If this came from
              Airbnb and is still active there, it will reappear on the next iCal sync.
            </p>
          </div>
        )}
      </RightPanel>
    </div>
  );
}
