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
import {
  BookingFunnelSection,
  GuestIntentSection,
  MiraImpactSection,
  NeedsConfirmationSection,
  PortfolioPerformanceSection,
  PricingSection,
} from "@/components/analytics/analytics-sections";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";

const ALL_PROPERTIES = "all";

/**
 * Host Analytics. One request (GET /analytics/dashboard) for every metric;
 * the "needs confirmation" list comes from the booking reconciliation queue
 * and opens the same panel the Calendar uses. Every number is computed
 * server-side from persisted data (backend/app/services/analytics_service.py)
 * -- nothing here derives or estimates a value.
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
        </div>
      </div>

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
        <>
          {queue && (
            <NeedsConfirmationSection
              queue={queue}
              propertyId={selectedProperty ?? null}
              onOpen={setOpenBookingId}
              expanded={queueExpanded}
              onToggleExpanded={() => setQueueExpanded((v) => !v)}
            />
          )}
          <PortfolioPerformanceSection data={data} onConfirmPrices={() => openFirstQueued((needsPrice) => needsPrice)} />
          <div className="grid items-stretch gap-4 lg:grid-cols-2">
            <BookingFunnelSection data={data} />
            <GuestIntentSection data={data} />
          </div>
          <MiraImpactSection data={data} onReviewMatches={() => openFirstQueued((_, needsMatch) => needsMatch)} />
          <PricingSection data={data} />
        </>
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
