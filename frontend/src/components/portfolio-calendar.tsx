"use client";

import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BlockDatesPanel, type BlockRequest } from "@/components/calendar/block-dates-panel";
import { BookingDetailsPanel } from "@/components/calendar/booking-details-panel";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Cell, CellTitle, Sheet, SubField, Toolbar } from "@/components/ui/sheet";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { cn, matchesSearch } from "@/lib/utils";
import { stayScopeLabel } from "@/lib/format";
import {
  bookingsMatching,
  findFreeProperties,
  sortProperties,
  type FinderResult,
  type SortOrder,
} from "@/lib/calendar-search";
import { bookingSourceColor, bookingsOnNight, daysInMonth, scopeMarkFor, toISODate } from "@/lib/calendar-grid";
import type { BookingOut, PropertyOut } from "@/lib/types";

const ROW_LIMIT = 20;
const FREE_SHOWN = 24;

/**
 * Every property's month at once: properties as rows, days as columns. This is the Calendar tab of the
 * Properties page. Clicking a property's name opens that property's own calendar (`onOpenProperty`).
 */
export function PortfolioCalendar({ onOpenProperty }: { onOpenProperty?: (property: PropertyOut) => void }) {
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const { data: properties, loading: loadingProperties } = useAsync(() => api.properties.list(), []);
  const { data: bookings, loading: loadingBookings, refetch } = useAsync(() => api.bookings.list(), []);
  // Unfiltered (no date range) so any past guest can be picked regardless of
  // when they last called -- this is a lookup list, not a report.
  const { data: guests } = useAsync(() => api.guests.list({}), []);

  const [block, setBlock] = useState<BlockRequest>({ nonce: 0, open: false, propertyId: "", checkIn: "", checkOut: "" });

  // Clicking a booked cell opens the booking's details (price, "Mira match?", unblock).
  const [selectedBookingId, setSelectedBookingId] = useState<string | null>(null);

  // Clicking a legend entry filters the grid down to just that source --
  // click again (or the same entry) to clear back to showing both. Lets a
  // host isolate e.g. just Airbnb bookings before going to update Airbnb's
  // own calendar, without hiding manual blocks from the grid permanently.
  const [sourceFilter, setSourceFilter] = useState<"airbnb" | "manual" | null>(null);

  // Search, sort and the "free from X to Y?" finder. All of it reads the bookings already on the page, with the
  // same overlap rule as the grid, so what the finder says is free is what the grid shows as free.
  const [search, setSearch] = useState("");
  const [sortOrder, setSortOrder] = useState<SortOrder>("default");
  const [finderFrom, setFinderFrom] = useState("");
  const [finderTo, setFinderTo] = useState("");
  const [finderGuests, setFinderGuests] = useState("");
  const [finder, setFinder] = useState<FinderResult | null>(null);
  const [finderError, setFinderError] = useState<string | null>(null);
  const [showAllFree, setShowAllFree] = useState(false);
  // 100 rows are unreadable, so the grid opens on the first few and the rest are a click (or a search) away.
  const [showAllRows, setShowAllRows] = useState(false);

  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const numDays = daysInMonth(year, month);
  const days = useMemo(() => Array.from({ length: numDays }, (_, i) => i + 1), [numDays]);

  const monthLabel = cursor.toLocaleDateString("en-IN", { month: "long", year: "numeric" });

  // See bookingsOnNight (lib/calendar-grid.ts) for why dates are compared as strings.
  function allBookingsForDay(propertyId: string, day: number) {
    const cellIso = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    return bookingsOnNight(bookings, propertyId, cellIso);
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
    const checkIn = day ? toISODate(new Date(year, month, day)) : "";
    const checkOut = day ? toISODate(new Date(year, month, day + 1)) : "";
    setBlock((current) => ({
      nonce: current.nonce + 1,
      open: true,
      propertyId: propertyId ?? properties?.[0]?.id ?? "",
      checkIn,
      checkOut,
    }));
  }

  /** What a cell stands for on a room-rented property (see scopeMarkFor). */
  function scopeMark(propertyId: string, day: number): string | null {
    return scopeMarkFor(allBookingsForDay(propertyId, day));
  }

  const guestMatches = useMemo(() => bookingsMatching(search, bookings ?? []), [search, bookings]);
  const visibleProperties = useMemo(() => {
    const guestPropertyIds = new Set(guestMatches.map((b) => b.property_id));
    const matching = (properties ?? []).filter(
      (property) =>
        matchesSearch(search, [property.name, property.city]) ||
        guestPropertyIds.has(property.id),
    );
    return sortProperties(matching, sortOrder);
  }, [properties, search, guestMatches, sortOrder]);
  const propertyName = (id: string) => properties?.find((p) => p.id === id)?.name ?? "A property";

  // How many properties have a guest or a block on each night of the month: the whole portfolio in one row.
  const occupancy = useMemo(() => {
    const perDay: Set<string>[] = Array.from({ length: numDays }, () => new Set());
    const mm = String(month + 1).padStart(2, "0");
    const monthStart = `${year}-${mm}-01`;
    const monthEnd = toISODate(new Date(year, month + 1, 1));
    for (const b of bookings ?? []) {
      if (b.status !== "confirmed" || b.check_in >= monthEnd || b.check_out <= monthStart) continue;
      for (let d = 1; d <= numDays; d++) {
        const iso = `${year}-${mm}-${String(d).padStart(2, "0")}`;
        if (iso >= b.check_in && iso < b.check_out) perDay[d - 1].add(b.property_id);
      }
    }
    return perDay.map((set) => set.size);
  }, [bookings, year, month, numDays]);

  const searching = search.trim() !== "";
  const rowsToShow = searching || showAllRows ? visibleProperties : visibleProperties.slice(0, ROW_LIMIT);

  function runFinder(e: React.FormEvent) {
    e.preventDefault();
    const guests = finderGuests.trim() === "" ? null : Number(finderGuests);
    const result = findFreeProperties(properties ?? [], bookings ?? [], finderFrom, finderTo, guests);
    if ("error" in result) {
      setFinder(null);
      setFinderError(result.error);
      return;
    }
    setFinderError(null);
    setFinder(result);
  }

  function clearFinder() {
    setFinder(null);
    setFinderError(null);
    setFinderFrom("");
    setFinderTo("");
    setFinderGuests("");
  }

  /** Show one month and (optionally) one booking's details. */
  function jumpTo(isoDate: string, bookingId?: string) {
    const [y, m] = isoDate.split("-").map(Number);
    setCursor(new Date(y, m - 1, 1));
    if (bookingId) setSelectedBookingId(bookingId);
  }

  const loading = loadingProperties || loadingBookings;
  const needsConfirmationCount = (bookings ?? []).filter(
    (b) => b.needs_price_confirmation || b.needs_attribution_review
  ).length;

  const todayIso = toISODate(new Date());
  const WEEKDAY = ["S", "M", "T", "W", "T", "F", "S"];

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <Button variant="cta" onClick={() => openBlockDialog()}>
          Block dates
        </Button>
      </div>

      <Sheet>
        <Toolbar className="justify-between gap-y-3">
          <div className="flex items-center gap-1 rounded-lg border border-input p-0.5">
            <Button variant="ghost" size="sm" aria-label="Previous month" onClick={() => setCursor(new Date(year, month - 1, 1))}>
              ←
            </Button>
            <span className="min-w-36 px-2 text-center text-sm font-semibold">{monthLabel}</span>
            <Button variant="ghost" size="sm" aria-label="Next month" onClick={() => setCursor(new Date(year, month + 1, 1))}>
              →
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] text-muted-foreground">
            <button
              type="button"
              onClick={() => toggleSourceFilter("airbnb")}
              title="Filter the grid to only Airbnb bookings — handy before updating Airbnb's own calendar"
              className={cn(
                "flex items-center gap-1.5 px-1.5 py-0.5 transition-colors hover:bg-accent",
                sourceFilter === "airbnb" && "bg-accent font-semibold text-foreground",
                sourceFilter === "manual" && "opacity-50"
              )}
            >
              <span className="inline-block size-3" style={{ background: bookingSourceColor.airbnb }} />
              Airbnb
            </button>
            <button
              type="button"
              onClick={() => toggleSourceFilter("manual")}
              title="Filter the grid to only manual blocks — handy before updating Airbnb's own calendar"
              className={cn(
                "flex items-center gap-1.5 px-1.5 py-0.5 transition-colors hover:bg-accent",
                sourceFilter === "manual" && "bg-accent font-semibold text-foreground",
                sourceFilter === "airbnb" && "opacity-50"
              )}
            >
              <span className="inline-block size-3" style={{ background: bookingSourceColor.manual }} />
              Manual block
            </button>
            {(properties ?? []).some((p) => p.room_rental_enabled) && (
              <span
                className="flex items-center gap-1.5"
                title="On properties rented by the room: E = the entire place is booked, 2R = 2 rooms are booked that night"
              >
                <span className="border border-border px-1 text-[11px] leading-4">E</span> entire place
                <span className="ml-1 border border-border px-1 text-[11px] leading-4">2R</span> rooms
              </span>
            )}
            {needsConfirmationCount > 0 && (
              <span
                className="flex items-center gap-1.5"
                title="Bookings with a missing final price or a possible Mira match -- click one to confirm"
              >
                <span className="inline-block size-1.5 rounded-full bg-(--status-pending)" />
                {needsConfirmationCount} need{needsConfirmationCount === 1 ? "s" : ""} confirmation
              </span>
            )}
            {sourceFilter && (
              <Button variant="ghost" size="sm" onClick={() => setSourceFilter(null)}>
                Clear filter
              </Button>
            )}
          </div>
        </Toolbar>

        <Toolbar className="gap-3">
          <div className="relative min-w-0 flex-1 sm:max-w-sm">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-8"
              placeholder="Search a property, city or guest"
              aria-label="Search the calendar"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search && (
              <button
                type="button"
                aria-label="Clear search"
                onClick={() => setSearch("")}
                className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
          <Select value={sortOrder} onValueChange={(v) => v && setSortOrder(v as SortOrder)}>
            <SelectTrigger className="w-40" aria-label="Sort properties">
              <SelectValue>{(value: string) => (value === "az" ? "A to Z" : value === "za" ? "Z to A" : "Default order")}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">Default order</SelectItem>
              <SelectItem value="az">A to Z</SelectItem>
              <SelectItem value="za">Z to A</SelectItem>
            </SelectContent>
          </Select>
        </Toolbar>

        <Cell className="gap-3">
          <CellTitle>Find free dates</CellTitle>
          <form onSubmit={runFinder} className="flex flex-wrap items-end gap-3">
            <SubField label="Check-in" htmlFor="finder-from">
              <Input id="finder-from" type="date" value={finderFrom} onChange={(e) => setFinderFrom(e.target.value)} />
            </SubField>
            <SubField label="Check-out" htmlFor="finder-to">
              <Input id="finder-to" type="date" value={finderTo} onChange={(e) => setFinderTo(e.target.value)} />
            </SubField>
            <SubField label="Guests" htmlFor="finder-guests">
              <Input
                id="finder-guests"
                type="number"
                min={1}
                className="w-24"
                placeholder="Any"
                value={finderGuests}
                onChange={(e) => setFinderGuests(e.target.value)}
              />
            </SubField>
            <Button type="submit">Find</Button>
            {(finder || finderError) && (
              <Button type="button" variant="ghost" onClick={clearFinder}>
                Clear
              </Button>
            )}
          </form>
          {finderError && (
            <p role="alert" className="text-sm text-destructive">
              {finderError}
            </p>
          )}
          {finder && (
            <div className="space-y-3">
              <p className="text-sm">
                <span className="font-semibold tabular-nums">{finder.free.length}</span> of{" "}
                {finder.free.length + finder.busy.length} free for {finder.nights} {finder.nights === 1 ? "night" : "nights"}
                {finder.guests ? `, ${finder.guests} guests` : ""}
              </p>
              {finder.free.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {(showAllFree ? finder.free : finder.free.slice(0, FREE_SHOWN)).map((property) => (
                    <button
                      key={property.id}
                      type="button"
                      onClick={() => onOpenProperty?.(property)}
                      title={`Open ${property.name}'s calendar`}
                      className="max-w-full truncate rounded-md border border-input bg-card px-2.5 py-1 text-left text-[13px] transition-colors hover:bg-accent"
                    >
                      {property.name}
                      <span className="ml-1.5 text-muted-foreground">sleeps {property.max_guests}</span>
                    </button>
                  ))}
                  {finder.free.length > FREE_SHOWN && (
                    <button
                      type="button"
                      onClick={() => setShowAllFree((v) => !v)}
                      className="px-1 py-1 text-[13px] text-muted-foreground underline underline-offset-2 hover:text-foreground"
                    >
                      {showAllFree ? "Show fewer" : `Show all ${finder.free.length}`}
                    </button>
                  )}
                </div>
              )}
              {finder.busy.length > 0 && (
                <details className="text-[13px]">
                  <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                    {finder.busy.length} not free
                  </summary>
                  <ul className="mt-2 max-h-56 space-y-1 overflow-y-auto">
                    {finder.busy.map(({ property, reason }) => (
                      <li key={property.id} className="flex flex-wrap justify-between gap-x-3">
                        <span className="min-w-0 truncate">{property.name}</span>
                        <span className="text-muted-foreground">{reason}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </Cell>

        {guestMatches.length > 0 && (
          <Cell className="gap-2">
            <CellTitle>Guest bookings</CellTitle>
            <ul className="max-h-48 overflow-y-auto">
              {guestMatches.slice(0, 20).map((b) => (
                <li key={b.id} className="border-b border-border last:border-b-0">
                  <button
                    type="button"
                    onClick={() => jumpTo(b.check_in, b.id)}
                    className="flex w-full flex-wrap justify-between gap-x-3 py-2 text-left text-[13px] hover:bg-accent"
                  >
                    <span className="min-w-0 truncate">
                      <span className="font-semibold">{b.guest_name ?? "Guest"}</span> · {propertyName(b.property_id)}
                    </span>
                    <span className="text-muted-foreground tabular-nums">
                      {b.check_in} → {b.check_out}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Cell>
        )}

        {loading ? (
          <Cell>
            <Skeleton className="h-96 w-full" />
          </Cell>
        ) : !properties || properties.length === 0 ? (
          <Cell>
            <p className="text-sm text-muted-foreground">No properties yet. Add one to see its calendar here.</p>
          </Cell>
        ) : (
          <Cell className="gap-0 p-0">
            <div className="max-h-[calc(100vh-14rem)] w-full overflow-auto">
              {/* table-layout: fixed + w-full ties the table's rendered width to
                  this box explicitly -- an auto-layout table has no contract
                  with its container's width, which is what produced the
                  "squished" look as the surrounding flex box resized (see
                  restructure.md Phase 5). The property column gets a fixed
                  width; day columns split the remainder evenly via inline
                  style since their count is dynamic (28-31, can't be a static
                  Tailwind class). min-w keeps the day cells readable on a
                  phone: the grid scrolls sideways there, the property column
                  stays pinned. */}
              <table className="w-full min-w-[960px] border-collapse text-sm" style={{ tableLayout: "fixed" }}>
                <colgroup>
                  <col style={{ width: "240px" }} />
                  {days.map((day) => (
                    <col key={day} style={{ width: `calc((100% - 240px) / ${numDays})` }} />
                  ))}
                </colgroup>
                <thead>
                  <tr>
                    <th className="sticky top-0 left-0 z-20 border-b border-border bg-card p-3 text-left text-xs font-bold tracking-[0.1em] uppercase">
                      Property
                    </th>
                    {days.map((day) => {
                      const dow = new Date(year, month, day).getDay();
                      const isToday = toISODate(new Date(year, month, day)) === todayIso;
                      return (
                        <th
                          key={day}
                          className={cn(
                            "sticky top-0 z-10 border-b border-l border-b-foreground border-l-border bg-card p-1 text-center text-xs font-normal text-muted-foreground tabular-nums",
                            (dow === 0 || dow === 6) && "bg-muted",
                            isToday && "font-bold text-foreground shadow-[inset_0_-3px_0_var(--primary)]"
                          )}
                        >
                          <span className="block text-[13px] leading-4">{day}</span>
                          <span className="block text-[11px] leading-4">{WEEKDAY[dow]}</span>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="sticky left-0 z-10 border-b border-border bg-card p-3 text-[13px] font-semibold">
                      All properties
                      <span className="block text-[11px] font-normal text-muted-foreground">booked each night</span>
                    </td>
                    {days.map((day, i) => {
                      const count = occupancy[i];
                      const share = properties.length ? count / properties.length : 0;
                      return (
                        <td
                          key={day}
                          title={`${count} of ${properties.length} properties booked`}
                          style={count ? { background: `color-mix(in srgb, var(--foreground) ${Math.round(8 + share * 40)}%, transparent)` } : undefined}
                          className="h-9 border-b border-l border-b-foreground border-l-border text-center text-xs tabular-nums"
                        >
                          {count || ""}
                        </td>
                      );
                    })}
                  </tr>
                  {rowsToShow.map((property: PropertyOut) => (
                    <tr key={property.id}>
                      <td className="sticky left-0 z-10 truncate border-b border-border bg-card p-0 text-[13px] font-semibold">
                        {onOpenProperty ? (
                          <button
                            type="button"
                            onClick={() => onOpenProperty(property)}
                            title={`Open ${property.name}'s calendar`}
                            className="block w-full p-3 text-left underline-offset-4 hover:underline"
                          >
                            <span className="block truncate">{property.name}</span>
                            {property.city && (
                              <span className="block truncate text-[11px] font-normal text-muted-foreground no-underline">{property.city}</span>
                            )}
                          </button>
                        ) : (
                          <span className="block truncate p-3">{property.name}</span>
                        )}
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
                        const dow = new Date(year, month, day).getDay();
                        const showMarker =
                          !!visibleBooking &&
                          (visibleBooking.needs_price_confirmation || visibleBooking.needs_attribution_review) &&
                          (visibleBooking.check_in === cellIso || (day === 1 && visibleBooking.check_in < cellIso));
                        return (
                          <td
                            key={day}
                            title={
                              visibleBooking
                                ? `${visibleBooking.platform} -- ${visibleBooking.guest_name ?? "no name"} -- ${stayScopeLabel(visibleBooking.stay_scope, visibleBooking.rooms_booked)} (${visibleBooking.check_in} → ${visibleBooking.check_out})${showMarker ? " -- needs confirmation" : ""} -- click for details`
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
                              "relative h-9 border-b border-l border-border",
                              !visibleBooking && (dow === 0 || dow === 6) && "bg-muted/60",
                              hiddenByFilter ? "cursor-not-allowed opacity-30" : "cursor-pointer",
                              !visibleBooking && !hiddenByFilter && "hover:bg-accent"
                            )}
                          >
                            {property.room_rental_enabled && visibleBooking && (
                              <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[11px] font-semibold text-foreground">
                                {scopeMark(property.id, day)}
                              </span>
                            )}
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
            {!searching && visibleProperties.length > ROW_LIMIT && (
              <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-2 text-[13px] text-muted-foreground">
                <span>
                  {showAllRows ? `All ${visibleProperties.length} properties` : `Showing ${ROW_LIMIT} of ${visibleProperties.length} properties`}
                </span>
                <Button variant="ghost" size="sm" onClick={() => setShowAllRows((v) => !v)}>
                  {showAllRows ? "Show fewer" : `Show all ${visibleProperties.length}`}
                </Button>
              </div>
            )}
            {searching && rowsToShow.length === 0 && (
              <p className="px-4 py-3 text-sm text-muted-foreground">No property matches.</p>
            )}
          </Cell>
        )}
      </Sheet>

      <BlockDatesPanel
        request={block}
        properties={properties ?? []}
        guests={guests ?? []}
        onOpenChange={(open) => setBlock((current) => ({ ...current, open }))}
        onCreated={refetch}
      />

      <BookingDetailsPanel bookingId={selectedBookingId} onClose={() => setSelectedBookingId(null)} onChanged={refetch} />
    </div>
  );
}
