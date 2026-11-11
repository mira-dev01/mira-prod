import type { BookingOut, PropertyOut } from "@/lib/types";

// Everything here compares plain YYYY-MM-DD strings, never Date objects: a Date parsed from "2026-12-04" is UTC
// midnight while a locally built one is local midnight, which in IST flips a checkout day to "booked".
// (The same rule the calendar grid and the backend's availability check use.)

export type SortOrder = "default" | "az" | "za";

export type FinderBusy = { property: PropertyOut; reason: string };

export type FinderResult = {
  from: string;
  to: string;
  nights: number;
  guests: number | null;
  /** Free for the whole range (and big enough, when a guest count was given), A to Z. */
  free: PropertyOut[];
  /** Not free, with the plain reason; A to Z. */
  busy: FinderBusy[];
};

/** Confirmed bookings (stays, blocks and payment holds alike) that overlap [from, to) for one property. */
export function blockingBookings(bookings: BookingOut[], propertyId: string, from: string, to: string): BookingOut[] {
  return bookings.filter(
    (b) => b.property_id === propertyId && b.status === "confirmed" && b.check_in < to && b.check_out > from,
  );
}

function byName(a: PropertyOut, b: PropertyOut): number {
  return a.name.localeCompare(b.name, "en", { sensitivity: "base", numeric: true });
}

export function sortProperties(properties: PropertyOut[], order: SortOrder): PropertyOut[] {
  if (order === "default") return properties;
  const sorted = [...properties].sort(byName);
  return order === "az" ? sorted : sorted.reverse();
}

export function nightsBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86_400_000);
}

function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
}

/**
 * Which properties are free from `from` to `to` (check-out day, not slept in). Returns an error message instead
 * of a result when the dates are not a valid range.
 */
export function findFreeProperties(
  properties: PropertyOut[],
  bookings: BookingOut[],
  from: string,
  to: string,
  guests: number | null,
): FinderResult | { error: string } {
  if (!from || !to) return { error: "Choose both dates." };
  const nights = nightsBetween(from, to);
  if (nights < 1) return { error: "Check-out must be after check-in." };
  if (guests !== null && (!Number.isInteger(guests) || guests < 1)) return { error: "Guests must be a whole number." };

  const free: PropertyOut[] = [];
  const busy: FinderBusy[] = [];
  for (const property of [...properties].sort(byName)) {
    const blocking = blockingBookings(bookings, property.id, from, to);
    if (blocking.length > 0) {
      const first = [...blocking].sort((a, b) => a.check_in.localeCompare(b.check_in))[0];
      const more = blocking.length > 1 ? ` and ${blocking.length - 1} more` : "";
      busy.push({ property, reason: `Booked ${shortDate(first.check_in)} to ${shortDate(first.check_out)}${more}` });
    } else if (guests !== null && guests > property.max_guests) {
      busy.push({ property, reason: `Sleeps up to ${property.max_guests}` });
    } else {
      free.push(property);
    }
  }
  return { from, to, nights, guests, free, busy };
}

/** Bookings whose guest name or phone matches the search text (2+ characters), soonest first. */
export function bookingsMatching(query: string, bookings: BookingOut[]): BookingOut[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  const digits = q.replace(/\D/g, "");
  return bookings
    .filter((b) => {
      if (b.status !== "confirmed") return false;
      if (b.guest_name && b.guest_name.toLowerCase().includes(q)) return true;
      // Digits only count as a phone search when enough were typed to mean something.
      return digits.length >= 3 && !!b.guest_phone && b.guest_phone.replace(/\D/g, "").includes(digits);
    })
    .sort((a, b) => a.check_in.localeCompare(b.check_in));
}
