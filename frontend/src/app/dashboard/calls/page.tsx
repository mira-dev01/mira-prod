"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { matchesSearch } from "@/lib/utils";
import { DateRangePicker } from "@/components/date-range-picker";
import { CallsTable, callTypeLabel } from "@/components/calls-table";

// Each option's callType maps 1:1 to the API's comma-separated call_type
// filter (app/api/v1/calls.py) -- "Qualified Calls" is the one option that's
// a grouping of several stored values rather than a single one, expressed
// the same way any other multi-value filter would be, no special-casing
// needed either here or on the backend. "All Calls" has no callType at all
// -- undefined means no filter is applied, so it's genuinely every call,
// Junk/Incomplete/Unknown included. Picking any other option filters down
// to exactly that type -- a plain inclusive filter, not a hide/reveal toggle.
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

export default function CallsPage() {
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const [activeTab, setActiveTab] = useState("all");
  const [search, setSearch] = useState("");
  const { startDateISO, endDateISO } = useDateRange();

  const activeFilter = CALL_LOG_FILTERS.find((f) => f.value === activeTab) ?? CALL_LOG_FILTERS[0];

  const { data: calls, loading } = useAsync(
    () =>
      api.calls.list({
        startDate: startDateISO,
        endDate: endDateISO,
        includeTestCalls,
        callType: activeFilter.callType,
      }),
    [startDateISO, endDateISO, includeTestCalls, activeTab]
  );

  // Client-side only -- every other filter above is server-side (calls
  // already come back scoped by date/type/test-call status), so search just
  // narrows the page already fetched, across every column the table shows.
  const filteredCalls = (calls ?? []).filter((call) =>
    matchesSearch(search, [
      call.caller_number,
      call.guest_name,
      call.guest_phone,
      call.status,
      call.urgency,
      callTypeLabel[call.call_type],
      call.classification_reason,
    ])
  );

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
        <Input
          placeholder="Search calls…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          leadingIcon={<Search />}
          className="w-64"
        />
      </div>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : !calls || calls.length === 0 ? (
        <p className="text-sm text-muted-foreground">No calls yet.</p>
      ) : filteredCalls.length === 0 ? (
        <p className="text-sm text-muted-foreground">No calls match your search.</p>
      ) : (
        <CallsTable calls={filteredCalls} />
      )}
    </div>
  );
}
