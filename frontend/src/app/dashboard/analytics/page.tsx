"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { cn } from "@/lib/utils";
import type { AnalyticsEntry } from "@/lib/types";

// Predefined sizes only, on a 12-column grid (full width on phones).
const SIZE_CLASS: Record<string, string> = {
  sm: "col-span-12 sm:col-span-6 lg:col-span-3",
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
        <div>
          <h1 className="page-title">Analytics</h1>
          <p className="text-sm text-muted-foreground">How your portfolio and Mira are performing.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Select value={propertyId} onValueChange={(v) => v && setPropertyId(v)}>
            <SelectTrigger className="w-52" aria-label="Property">
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
        <Card>
          <CardContent className="space-y-3 py-6 text-sm">
            <p className="font-medium">No properties yet</p>
            <p className="text-muted-foreground">
              Add a property and connect its calendar — Analytics fills in as bookings and Mira conversations come in.
            </p>
            <Button render={<Link href="/dashboard/properties" />} size="sm">
              Add a property
            </Button>
          </CardContent>
        </Card>
      ) : error ? (
        <Card>
          <CardContent className="space-y-3 py-6 text-sm">
            <p className="font-medium">Couldn&apos;t load analytics</p>
            <p className="text-muted-foreground">Something went wrong fetching these numbers. Try again.</p>
            <Button size="sm" variant="outline" onClick={refetchDashboard}>
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : loading || !data ? (
        <div className="space-y-4" aria-busy="true" aria-label="Loading analytics">
          <Skeleton className="h-44 w-full" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton className="h-72 w-full" />
            <Skeleton className="h-72 w-full" />
          </div>
          <Skeleton className="h-56 w-full" />
        </div>
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
          className="block border-t pt-4 text-xs text-muted-foreground underline-offset-4 hover:underline"
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
      <Card>
        <CardContent className="space-y-3 py-6 text-sm">
          <p className="text-muted-foreground">Couldn&apos;t load your Analytics layout.</p>
          <Button size="sm" variant="outline" onClick={onRetryLayout}>
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing on your Analytics page yet — use Customize to add metrics.</p>;
  }
  return (
    <div className="grid grid-cols-12 items-stretch gap-4">
      {entries.map((entry) => {
        if (entry.type === "heading") {
          return (
            <div key={entry.id} className="col-span-12 pt-2">
              <h2 className="font-heading text-base font-medium">{entry.title}</h2>
              {entry.subtitle && <p className="text-sm text-muted-foreground">{entry.subtitle}</p>}
            </div>
          );
        }
        const content = renderAnalyticsMetric(entry, context);
        if (content === null) return null;
        return (
          <div key={entry.id} className={cn("min-w-0", SIZE_CLASS[entry.size ?? "full"])}>
            {content}
          </div>
        );
      })}
    </div>
  );
}
