"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { BookingOut, PropertyOut } from "@/lib/types";

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

  const [blockOpen, setBlockOpen] = useState(false);
  const [blockPropertyId, setBlockPropertyId] = useState<string>("");
  const [blockCheckIn, setBlockCheckIn] = useState("");
  const [blockCheckOut, setBlockCheckOut] = useState("");
  const [blockGuestName, setBlockGuestName] = useState("");
  const [submitting, setSubmitting] = useState(false);

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

      <div className="flex items-center gap-3">
        <Button variant="outline" size="sm" onClick={() => setCursor(new Date(year, month - 1, 1))}>
          ←
        </Button>
        <span className="text-sm font-medium">{monthLabel}</span>
        <Button variant="outline" size="sm" onClick={() => setCursor(new Date(year, month + 1, 1))}>
          →
        </Button>
        <div className="ml-4 flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: "var(--chart-3)" }} />
            Airbnb
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: "var(--chart-4)" }} />
            Manual block
          </span>
        </div>
      </div>

      {loading ? (
        <Skeleton className="h-96 w-full" />
      ) : !properties || properties.length === 0 ? (
        <p className="text-sm text-muted-foreground">No properties yet — add one to see its calendar here.</p>
      ) : (
        <div className="overflow-x-auto rounded-[var(--radius)] border">
          <table className="border-collapse text-sm">
            <thead>
              <tr>
                <th className="sticky left-0 z-10 min-w-[140px] border-b bg-card p-2 text-left">Property</th>
                {days.map((day) => (
                  <th key={day} className="min-w-[28px] border-b border-l p-1 text-center text-xs font-normal text-muted-foreground">
                    {day}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {properties.map((property: PropertyOut) => (
                <tr key={property.id}>
                  <td className="sticky left-0 z-10 min-w-[140px] border-b bg-card p-2 font-medium">
                    {property.name}
                  </td>
                  {days.map((day) => {
                    const dayBookings = bookingsForDay(property.id, day);
                    const booking = dayBookings[0];
                    return (
                      <td
                        key={day}
                        title={
                          booking
                            ? `${booking.platform} -- ${booking.guest_name ?? "no name"} (${booking.check_in} → ${booking.check_out})`
                            : "Available -- click to block"
                        }
                        onClick={() => openBlockDialog(property.id, day)}
                        className={cn(
                          "h-7 min-w-[28px] cursor-pointer border-b border-l",
                          booking &&
                            (booking.platform === "airbnb"
                              ? "bg-[var(--chart-3)]/70 hover:bg-[var(--chart-3)]"
                              : "bg-[var(--chart-4)]/70 hover:bg-[var(--chart-4)]"),
                          !booking && "hover:bg-accent"
                        )}
                      />
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={blockOpen} onOpenChange={setBlockOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Block dates</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleBlockDates} className="space-y-4">
            <div className="space-y-2">
              <Label>Property</Label>
              <Select value={blockPropertyId} onValueChange={(v) => v && setBlockPropertyId(v)}>
                <SelectTrigger>
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
              <Input
                id="block-guest-name"
                value={blockGuestName}
                onChange={(e) => setBlockGuestName(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={submitting}>
                {submitting ? "Blocking…" : "Block dates"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
