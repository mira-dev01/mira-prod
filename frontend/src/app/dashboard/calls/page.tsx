"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { DateRangePicker } from "@/components/date-range-picker";
import { CallsTable } from "@/components/calls-table";

// Each tab's callType maps 1:1 to the API's comma-separated call_type filter
// (app/api/v1/calls.py) -- "Qualified Calls" is the one tab that's a
// grouping of several stored values rather than a single one, expressed the
// same way any other multi-value filter would be, no special-casing needed
// either here or on the backend.
const CALL_LOG_FILTERS: { value: string; label: string; callType?: string }[] = [
  { value: "all", label: "All Calls" },
  { value: "qualified", label: "Qualified Calls", callType: "BOOKING_LEAD,GUEST_SUPPORT,EXISTING_BOOKING,GENERAL_QUERY" },
  { value: "booking", label: "Booking Leads", callType: "BOOKING_LEAD" },
  { value: "support", label: "Guest Support", callType: "GUEST_SUPPORT" },
  { value: "existing", label: "Existing Guests", callType: "EXISTING_BOOKING" },
  { value: "incomplete", label: "Incomplete", callType: "INCOMPLETE" },
  { value: "junk", label: "Junk", callType: "JUNK" },
  { value: "unknown", label: "Unknown", callType: "UNKNOWN" },
];

// Tabs that already target one of the 3 normally-hidden categories directly
// -- toggling "Show Junk" while already viewing the Junk tab would be a
// confusing double negative, so the Hidden Filters chip is disabled there.
const HIDDEN_CATEGORY_TABS: Record<string, "junk" | "incomplete" | "unknown"> = {
  junk: "junk",
  incomplete: "incomplete",
  unknown: "unknown",
};

export default function CallsPage() {
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const [activeTab, setActiveTab] = useState("all");
  const [showJunk, setShowJunk] = useState(false);
  const [showIncomplete, setShowIncomplete] = useState(false);
  const [showUnknown, setShowUnknown] = useState(false);
  const { startDateISO, endDateISO } = useDateRange();

  const activeFilter = CALL_LOG_FILTERS.find((f) => f.value === activeTab) ?? CALL_LOG_FILTERS[0];
  const hiddenCategoryTab = HIDDEN_CATEGORY_TABS[activeTab];

  // On "All Calls" (or any qualified/specific-qualified-type tab), Junk/
  // Incomplete/Unknown are excluded by default and only included if their
  // toggle is on. A tab that already targets one of those three directly
  // (e.g. "Junk") shows exactly that type regardless of the toggles --
  // there's nothing to hide/reveal within a single already-explicit type.
  function effectiveCallType(): string | undefined {
    if (hiddenCategoryTab) return activeFilter.callType;
    if (!activeFilter.callType) {
      // "All Calls": start from every value, drop whichever of the 3 are
      // still hidden.
      const hidden = new Set<string>();
      if (!showJunk) hidden.add("JUNK");
      if (!showIncomplete) hidden.add("INCOMPLETE");
      if (!showUnknown) hidden.add("UNKNOWN");
      if (hidden.size === 0) return undefined;
      const allTypes = ["BOOKING_LEAD", "GUEST_SUPPORT", "EXISTING_BOOKING", "GENERAL_QUERY", "JUNK", "INCOMPLETE", "UNKNOWN"];
      return allTypes.filter((t) => !hidden.has(t)).join(",");
    }
    // A specific qualified-type tab (Booking Leads, Guest Support, Existing
    // Guests) or "Qualified Calls" itself -- none of these ever include
    // Junk/Incomplete/Unknown, so the toggles don't apply; just use the
    // tab's own callType as-is.
    return activeFilter.callType;
  }

  const { data: calls, loading } = useAsync(
    () =>
      api.calls.list({
        startDate: startDateISO,
        endDate: endDateISO,
        includeTestCalls,
        callType: effectiveCallType(),
      }),
    [startDateISO, endDateISO, includeTestCalls, activeTab, showJunk, showIncomplete, showUnknown]
  );

  const hiddenCount = [showJunk, showIncomplete, showUnknown].filter((shown) => !shown).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">Calls</h1>
          <p className="text-sm text-muted-foreground">Every call MIRA has answered across your properties</p>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <Switch id="include-test-calls" checked={includeTestCalls} onCheckedChange={setIncludeTestCalls} />
            <Label htmlFor="include-test-calls" className="text-sm text-muted-foreground">
              Include browser test calls
            </Label>
          </div>
          <DateRangePicker />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Select value={activeTab} onValueChange={(v) => v && setActiveTab(v)}>
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CALL_LOG_FILTERS.map((f) => (
              <SelectItem key={f.value} value={f.value}>
                {f.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Popover>
          <PopoverTrigger
            render={
              <Button variant="outline" size="sm" disabled={Boolean(hiddenCategoryTab)}>
                Hidden Filters ({hiddenCategoryTab ? 3 : hiddenCount})
              </Button>
            }
          />
          <PopoverContent align="start" className="w-64">
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="show-junk" className="text-sm font-normal">
                  Show Junk
                </Label>
                <Switch id="show-junk" checked={showJunk} onCheckedChange={setShowJunk} />
              </div>
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="show-incomplete" className="text-sm font-normal">
                  Show Incomplete
                </Label>
                <Switch id="show-incomplete" checked={showIncomplete} onCheckedChange={setShowIncomplete} />
              </div>
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="show-unknown" className="text-sm font-normal">
                  Show Unknown
                </Label>
                <Switch id="show-unknown" checked={showUnknown} onCheckedChange={setShowUnknown} />
              </div>
            </div>
          </PopoverContent>
        </Popover>
      </div>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : !calls || calls.length === 0 ? (
        <p className="text-sm text-muted-foreground">No calls yet.</p>
      ) : (
        <CallsTable calls={calls} />
      )}
    </div>
  );
}
