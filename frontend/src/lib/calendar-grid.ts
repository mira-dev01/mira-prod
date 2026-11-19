import type { BookingOut } from "@/lib/types";

// Booking-source colours. Deliberately NOT drawn from the StatusTone system (live / pending / progress /
// destructive): those carry an urgency meaning elsewhere that doesn't apply here -- Airbnb-vs-manual is a
// category, not a status. Reuses the chart palette, centralised so the legend and the cells share one lookup.
export const bookingSourceColor = {
  airbnb: "var(--chart-3)",
  manual: "var(--chart-4)",
} as const;

/** Local calendar day as YYYY-MM-DD. toISOString() is UTC, which in IST turns a local-midnight date into the
 * previous day (the block form was once prefilled a day early). */
export function toISODate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

/**
 * The confirmed bookings that occupy `property` on the night of `isoDate`. Compared as YYYY-MM-DD strings, NOT
 * Dates: `new Date("2026-08-01")` is UTC midnight while `new Date(y, m, d)` is local midnight, so in IST a
 * booking's check-out day (meant to be free) used to flip to blocked. check_in/check_out are plain dates from
 * the backend, so string comparison sidesteps time zones entirely. Check-out day is free.
 */
export function bookingsOnNight(bookings: BookingOut[] | null | undefined, propertyId: string, isoDate: string): BookingOut[] {
  return (bookings ?? []).filter(
    (b) => b.property_id === propertyId && b.status === "confirmed" && isoDate >= b.check_in && isoDate < b.check_out
  );
}

/** What a cell stands for on a room-rented property: "E" when the entire place is taken, else the number of
 * rooms booked that night ("2R"). */
export function scopeMarkFor(booked: BookingOut[]): string | null {
  if (booked.length === 0) return null;
  if (booked.some((b) => b.stay_scope !== "rooms")) return "E";
  return `${booked.reduce((sum, b) => sum + (b.rooms_booked ?? 1), 0)}R`;
}
