"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Cell, Hint, Sheet, SheetHeading } from "@/components/ui/sheet";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { DateRangePicker } from "@/components/date-range-picker";
import { BookingReconciliationPanel } from "@/components/booking-reconciliation";
import { LayoutGrid } from "lucide-react";
import { renderAnalyticsMetric, type AnalyticsWidgetContext } from "@/components/analytics/analytics-sections";
import {
  AnalyticsCustomizer,
  analyticsDraftFrom,
  type AnalyticsDraft,
} from "@/components/analytics/analytics-customizer";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import type { AnalyticsEntry } from "@/lib/types";

// Predefined sizes only, on a 12-column grid (full width on phones).
const SIZE_CLASS: Record<string, string> = {
  sm: "col-span-6 p-4 sm:p-5 lg:col-span-3",
  md: "col-span-12 lg:col-span-6",
  half: "col-span-12 lg:col-span-6",
  full: "col-span-12",
};

const ALL_PROPERTIES = "all";

/**
 * Host Analytics. One request (GET /analytics/dashboard) for every metric;
 * the "needs confirmation" list comes from the booking reconciliation queue
 * and opens the same panel the Calendar uses. Every number is computed
 * server-side from persisted data (backend/app/services/analytics_service.py)
 * -- nothing here derives or estimates a value. Which metrics show, in what
 * order and size, and the headings between them are the host's own layout
 * (GET /preferences/analytics-widgets); the layout never changes a number.
 */
export default function AnalyticsPage() {
  const { startDateISO, endDateISO } = useDateRange();
  const [propertyId, setPropertyId] = useState<string>(ALL_PROPERTIES);
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const [openBookingId, setOpenBookingId] = useState<string | null>(null);
  const [queueExpanded, setQueueExpanded] = useState(false);

  const { data: properties, loading: propertiesLoading } = useAsync(() => api.properties.list(), []);
  const selectedProperty = propertyId === ALL_PROPERTIES ? undefined : propertyId;
  const {
    data,
    loading,
    error,
    refetch: refetchDashboard,
  } = useAsync(
    () =>
      api.analytics.dashboard({
        startDate: startDateISO,
        endDate: endDateISO,
        propertyId: selectedProperty,
        includeTestCalls,
      }),
    [startDateISO, endDateISO, selectedProperty, includeTestCalls]
  );
  const { data: queue, refetch: refetchQueue } = useAsync(() => api.bookings.reconciliationQueue(), []);
  const {
    data: layout,
    error: layoutError,
    refetch: refetchLayout,
    setData: setLayout,
  } = useAsync(() => api.preferences.analyticsWidgets(), []);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<AnalyticsDraft | null>(null);
  const activeDraft = editing && layout ? (draft ?? analyticsDraftFrom(layout)) : null;

  function openFirstQueued(predicate: (needsPrice: boolean, needsMatch: boolean) => boolean) {
    const item = queue?.items.find(
      (i) =>
        (!selectedProperty || i.booking.property_id === selectedProperty) &&
        predicate(i.booking.needs_price_confirmation, i.booking.needs_attribution_review)
    );
    if (item) {
      setOpenBookingId(item.booking.id);
    } else {
      document.getElementById("needs-confirmation")?.scrollIntoView({ behavior: "smooth" });
    }
  }

  const noProperties = !propertiesLoading && (properties?.length ?? 0) === 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <h1 className="page-title">Analytics</h1>
        <div className="flex flex-wrap items-center gap-3">
          <Select value={propertyId} onValueChange={(v) => v && setPropertyId(v)}>
            <SelectTrigger className="w-full sm:w-52" aria-label="Property">
              <SelectValue>
                {(value: string) =>
                  value === ALL_PROPERTIES
                    ? "All properties"
                    : (properties?.find((p) => p.id === value)?.name ?? "All properties")
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_PROPERTIES}>All properties</SelectItem>
              {properties?.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DateRangePicker />
          <div className="flex items-center gap-2">
            <Switch id="analytics-include-test-calls" checked={includeTestCalls} onCheckedChange={setIncludeTestCalls} />
            <Label htmlFor="analytics-include-test-calls" className="text-sm text-muted-foreground">
              Include browser test calls
            </Label>
          </div>
          {layout && !activeDraft && (
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <LayoutGrid className="size-3.5" />
              Customize
            </Button>
          )}
        </div>
      </div>

      {activeDraft && layout && (
        <AnalyticsCustomizer
          layout={layout}
          draft={activeDraft}
          onDraftChange={setDraft}
          onSaved={(saved) => {
            setLayout(saved);
            setEditing(false);
            setDraft(null);
          }}
          onCancel={() => {
            setEditing(false);
            setDraft(null);
          }}
          onReload={() => {
            setDraft(null);
            refetchLayout();
          }}
        />
      )}

      {noProperties ? (
        <Sheet>
          <Cell>
            <p className="text-sm font-semibold">No properties yet</p>
            <Hint>Analytics fills in as bookings and Mira conversations come in.</Hint>
            <Button render={<Link href="/dashboard/properties" />} variant="cta" className="self-start">
              Add a property
            </Button>
          </Cell>
        </Sheet>
      ) : error ? (
        <Sheet>
          <Cell>
            <p className="text-sm font-semibold">Couldn&apos;t load analytics</p>
            <Hint>Something went wrong fetching these numbers.</Hint>
            <Button variant="outline" className="self-start" onClick={refetchDashboard}>
              Retry
            </Button>
          </Cell>
        </Sheet>
      ) : loading || !data ? (
        <Sheet aria-busy="true" aria-label="Loading analytics">
          {[0, 1, 2, 3].map((i) => (
            <Cell key={i} className="col-span-6 lg:col-span-3">
              <Skeleton className="h-16 w-full" />
            </Cell>
          ))}
          {[0, 1].map((i) => (
            <Cell key={i} className="lg:col-span-6">
              <Skeleton className="h-48 w-full" />
            </Cell>
          ))}
        </Sheet>
      ) : (
        <AnalyticsGrid
          entries={
            activeDraft
              ? activeDraft.shown
              : layout
                ? layout.entries.filter((e) => e.type === "heading" || (e.available && !e.hidden))
                : layoutError
                  ? null
                  : []
          }
          context={{
            data,
            queue: queue ?? null,
            propertyId: selectedProperty ?? null,
            queueExpanded,
            onToggleQueue: () => setQueueExpanded((v) => !v),
            onOpenBooking: setOpenBookingId,
            onConfirmPrices: () => openFirstQueued((needsPrice) => needsPrice),
            onReviewMatches: () => openFirstQueued((_, needsMatch) => needsMatch),
          }}
          onRetryLayout={refetchLayout}
        />
      )}

      <BookingReconciliationPanel
        bookingId={openBookingId}
        onOpenChange={(open) => !open && setOpenBookingId(null)}
        onChanged={() => {
          refetchQueue();
          refetchDashboard();
        }}
      >
        <Link
          href="/dashboard/calendar"
          className="block text-[13px] font-semibold text-foreground underline underline-offset-4 hover:no-underline"
        >
          Open the Calendar
        </Link>
      </BookingReconciliationPanel>
    </div>
  );
}

/**
 * The host's layout, in order: headings span the full width; metrics take
 * their chosen predefined size. Headings with nothing under them are kept --
 * they're the host's own content.
 */
function AnalyticsGrid({
  entries,
  context,
  onRetryLayout,
}: {
  entries: AnalyticsEntry[] | null;
  context: AnalyticsWidgetContext;
  onRetryLayout: () => void;
}) {
  if (entries === null) {
    return (
      <Sheet>
        <Cell>
          <p className="text-sm text-muted-foreground">Couldn&apos;t load your Analytics layout.</p>
          <Button variant="outline" className="self-start" onClick={onRetryLayout}>
            Retry
          </Button>
        </Cell>
      </Sheet>
    );
  }
  if (entries.length === 0) {
    return (
      <Sheet>
        <Cell>
          <p className="text-sm text-muted-foreground">Nothing on your Analytics page yet. Use Customize to add metrics.</p>
        </Cell>
      </Sheet>
    );
  }
  return (
    <Sheet>
      {entries.map((entry) => {
        if (entry.type === "heading") {
          return (
            <SheetHeading key={entry.id} title={entry.title ?? ""} className="border-r border-b border-foreground bg-card/70">
              {entry.subtitle}
            </SheetHeading>
          );
        }
        const content = renderAnalyticsMetric(entry, context);
        if (content === null) return null;
        return (
          <Cell key={entry.id} className={SIZE_CLASS[entry.size ?? "full"]}>
            {content}
          </Cell>
        );
      })}
    </Sheet>
  );
}
