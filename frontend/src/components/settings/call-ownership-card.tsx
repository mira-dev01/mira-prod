"use client";

import { Fragment, useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip } from "@/components/status-chip";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CallHandlingMode, PropertyOut } from "@/lib/types";

// Same short, curated zone list as property-form-fields.tsx's Call Handling
// section (this codebase's only other call-ownership editor) -- an
// India-only host fleet plus common Airbnb host regions, not the full IANA
// database. Backend validation (app/schemas/property.py's _check_timezone)
// is authoritative regardless of what's offered here.
const TIMEZONES = [
  { value: "Asia/Kolkata", label: "India Standard Time (Asia/Kolkata)" },
  { value: "Asia/Dubai", label: "Gulf Standard Time (Asia/Dubai)" },
  { value: "Asia/Kathmandu", label: "Nepal Time (Asia/Kathmandu)" },
  { value: "Asia/Colombo", label: "Sri Lanka Time (Asia/Colombo)" },
  { value: "Europe/London", label: "UK Time (Europe/London)" },
  { value: "America/New_York", label: "US Eastern Time (America/New_York)" },
  { value: "America/Los_Angeles", label: "US Pacific Time (America/Los_Angeles)" },
  { value: "Asia/Singapore", label: "Singapore Time (Asia/Singapore)" },
  { value: "Australia/Sydney", label: "Australia Eastern Time (Australia/Sydney)" },
  { value: "UTC", label: "UTC" },
] as const;

const MODES: { value: CallHandlingMode; label: string; confirmation: string }[] = [
  { value: "MIRA", label: "Mira always", confirmation: "Mira will answer calls at all times." },
  { value: "HOST", label: "Host always", confirmation: "Calls will go directly to your transfer number." },
  { value: "SCHEDULED", label: "Scheduled", confirmation: "" },
];

const MODE_SUMMARY: Record<CallHandlingMode, { label: string; tone: "live" | "orange" | "neutral" }> = {
  MIRA: { label: "Mira always", tone: "live" },
  HOST: { label: "Host always", tone: "orange" },
  SCHEDULED: { label: "Scheduled", tone: "neutral" },
};

// TEMPORARY: while backend/app/services/call_ownership.py's fixed-hours
// override (Settings.fixed_host_hours_start/_end) is active, every
// property is forced onto one global 11 AM-5 PM host window regardless of
// what's configured here -- this per-property editor's Save still works
// but has no effect until the override is removed. Delete this banner and
// the `disabled` prop plumbing below in the same change that removes that
// backend override.
const FIXED_HOURS_BANNER =
  "Host hours are currently fixed to 11:00 AM–5:00 PM for all properties; per-property scheduling is temporarily unavailable.";

function FixedHoursBanner() {
  return (
    <div className="rounded-md border border-dashed bg-muted/50 p-3 text-sm text-muted-foreground">
      {FIXED_HOURS_BANNER}
    </div>
  );
}

function formatHourMinute(value: string | null): string {
  if (!value) return "--";
  const [hourStr, minuteStr] = value.split(":");
  const hour = Number(hourStr);
  const suffix = hour >= 12 ? "PM" : "AM";
  const hour12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${hour12}:${minuteStr} ${suffix}`;
}

function timezoneLabel(tz: string): string {
  return TIMEZONES.find((t) => t.value === tz)?.label ?? tz;
}

type FormState = {
  call_handling_mode: CallHandlingMode;
  call_handling_schedule_start: string;
  call_handling_schedule_end: string;
  timezone: string;
};

function formFromProperty(property: PropertyOut): FormState {
  return {
    call_handling_mode: property.call_handling_mode,
    // Empty, not a silent 09:00/18:00 guess -- matches property-form-
    // fields.tsx's existing (already-reviewed) Call Handling section,
    // which leaves these blank when unset rather than pre-filling a
    // default the host never actually chose. The <input required> below
    // then blocks Save until the host deliberately enters both times --
    // important because this window controls when calls go to the HOST's
    // own phone, not a cosmetic default.
    call_handling_schedule_start: property.call_handling_schedule_start ?? "",
    call_handling_schedule_end: property.call_handling_schedule_end ?? "",
    timezone: property.timezone,
  };
}

function ScheduleEditor({
  form,
  onChange,
  disabled,
}: {
  form: FormState;
  onChange: (next: FormState) => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-4 rounded-lg border bg-muted/50 p-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="space-y-2">
          <Label htmlFor="schedule-start">You answer from</Label>
          <Input
            id="schedule-start"
            type="time"
            required
            disabled={disabled}
            value={form.call_handling_schedule_start}
            onChange={(e) => onChange({ ...form, call_handling_schedule_start: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="schedule-end">You answer until</Label>
          <Input
            id="schedule-end"
            type="time"
            required
            disabled={disabled}
            value={form.call_handling_schedule_end}
            onChange={(e) => onChange({ ...form, call_handling_schedule_end: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="schedule-timezone">Timezone</Label>
          <Select
            value={form.timezone}
            onValueChange={(v) => v && onChange({ ...form, timezone: v })}
            disabled={disabled}
          >
            <SelectTrigger id="schedule-timezone" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TIMEZONES.map((tz) => (
                <SelectItem key={tz.value} value={tz.value}>
                  {tz.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        These hours apply every day. An overnight window (e.g. 10:00 PM – 6:00 AM) is fine — it stays in
        effect across midnight.
      </p>

      {form.call_handling_schedule_start && form.call_handling_schedule_end ? (
        <div className="rounded-md border border-dashed bg-background p-3 text-sm">
          <p>
            Calls go to the host <span className="font-medium">{formatHourMinute(form.call_handling_schedule_start)}</span>
            {" – "}
            <span className="font-medium">{formatHourMinute(form.call_handling_schedule_end)}</span>
            {" "}
            ({timezoneLabel(form.timezone)}).
          </p>
          <p className="text-muted-foreground">Outside these hours, Mira answers calls.</p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Enter both times to see a preview.</p>
      )}
    </div>
  );
}

// One property's expanded editor -- own form/saving/error state, keyed by
// property.id from the parent map so switching which row is expanded never
// carries stale edits from a different property into this instance.
function PropertyOwnershipEditor({
  property,
  onSaved,
  disabled,
}: {
  property: PropertyOut;
  onSaved: () => Promise<void>;
  disabled?: boolean;
}) {
  const [form, setForm] = useState<FormState>(() => formFromProperty(property));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- resyncing local edits when the underlying property data changes (e.g. after a save/refetch) is the point of this effect
    setForm(formFromProperty(property));
  }, [property]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const payload =
        form.call_handling_mode === "SCHEDULED"
          ? {
              call_handling_mode: form.call_handling_mode,
              call_handling_schedule_start: form.call_handling_schedule_start,
              call_handling_schedule_end: form.call_handling_schedule_end,
              timezone: form.timezone,
            }
          : { call_handling_mode: form.call_handling_mode };
      await api.properties.update(property.id, payload);
      await onSaved();
      toast.success(`Call ownership saved for ${property.name}`);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to save call ownership";
      setError(message);
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4 border-t bg-muted/20 p-4">
      <div className="flex flex-wrap gap-2">
        {MODES.map((mode) => (
          <Button
            key={mode.value}
            type="button"
            variant={form.call_handling_mode === mode.value ? "default" : "outline"}
            size="sm"
            aria-pressed={form.call_handling_mode === mode.value}
            disabled={disabled}
            className={cn(form.call_handling_mode === mode.value && "pointer-events-none")}
            onClick={() => setForm({ ...form, call_handling_mode: mode.value })}
          >
            {mode.label}
          </Button>
        ))}
      </div>

      {form.call_handling_mode !== "SCHEDULED" && (
        <p className="text-sm text-muted-foreground">
          {MODES.find((m) => m.value === form.call_handling_mode)?.confirmation}
        </p>
      )}

      {form.call_handling_mode === "SCHEDULED" && (
        <ScheduleEditor form={form} onChange={setForm} disabled={disabled} />
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex justify-end">
        <Button type="button" onClick={handleSave} disabled={disabled || saving}>
          {saving ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </div>
  );
}

export function CallOwnershipCard() {
  const { data: properties, loading, refetch } = useAsync(() => api.properties.list(), []);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // properties === null (not the `loading` flag) gates the skeleton --
  // `loading` flips true again on every refetch() too (see use-async.ts),
  // and the existing reference components in this codebase (properties/
  // page.tsx, technicians-section.tsx) never gate their main content on it
  // past the initial mount, letting stale data show through during a
  // background refetch instead.
  if (properties === null && loading) {
    return (
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Call ownership</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton variant="text" />
          <Skeleton variant="text" className="w-2/3" />
        </CardContent>
      </Card>
    );
  }

  if (!properties || properties.length === 0) {
    return (
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Call ownership</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Add a property first, then choose when Mira answers guest calls and when you take them yourself.
          </p>
        </CardContent>
      </Card>
    );
  }

  // Single-property hosts skip the table entirely -- one row to click open
  // is just friction when there's only ever one option, and the editor is
  // shown expanded by default instead.
  if (properties.length === 1) {
    const property = properties[0];
    return (
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Call ownership</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <FixedHoursBanner />
          <p className="text-sm text-muted-foreground">
            Choose when Mira answers guest calls and when you take them yourself.
          </p>
          <PropertyOwnershipEditor property={property} onSaved={async () => void (await refetch())} disabled />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Call ownership</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <FixedHoursBanner />
        <p className="text-sm text-muted-foreground">
          Choose when Mira answers guest calls and when you take them yourself, per property. Click a row
          to configure it.
        </p>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Property</TableHead>
              <TableHead>Call ownership</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {properties.map((property) => {
              const isExpanded = expandedId === property.id;
              const summary = MODE_SUMMARY[property.call_handling_mode];
              return (
                <Fragment key={property.id}>
                  <TableRow
                    role="button"
                    tabIndex={0}
                    aria-expanded={isExpanded}
                    className="cursor-pointer"
                    onClick={() => setExpandedId(isExpanded ? null : property.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setExpandedId(isExpanded ? null : property.id);
                      }
                    }}
                  >
                    <TableCell className="font-medium whitespace-normal">{property.name}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusChip status={summary.label} tone={summary.tone} />
                        {property.call_handling_mode === "SCHEDULED" &&
                          property.call_handling_schedule_start &&
                          property.call_handling_schedule_end && (
                            <span className="text-xs text-muted-foreground">
                              Host {formatHourMinute(property.call_handling_schedule_start)}
                              {" – "}
                              {formatHourMinute(property.call_handling_schedule_end)}
                            </span>
                          )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <ChevronDown
                        className={cn("size-4 text-muted-foreground transition-transform", isExpanded && "rotate-180")}
                      />
                    </TableCell>
                  </TableRow>
                  {isExpanded && (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={3} className="whitespace-normal p-0">
                        <PropertyOwnershipEditor
                          property={property}
                          onSaved={async () => void (await refetch())}
                          disabled
                        />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
