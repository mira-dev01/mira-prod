"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { PropertyCombobox } from "@/components/property-combobox";
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

export function CallOwnershipCard() {
  const { data: properties, loading, refetch } = useAsync(() => api.properties.list(), []);

  const [propertyId, setPropertyId] = useState("");
  const [form, setForm] = useState<FormState | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Tracks which property's data `form` currently mirrors -- React's own
  // "adjust state during render" pattern (see react.dev/learn/you-might-
  // not-need-an-effect#adjusting-some-state-when-a-prop-changes) instead of
  // a useEffect, so switching properties re-derives `form` synchronously in
  // the same render rather than flashing the previous property's stale
  // values for one frame. Single-property hosts auto-select that property
  // the same way -- the common case shouldn't require picking from a list
  // of one -- while multi-property hosts land with nothing selected, since
  // applying one property's schedule to "the" account would silently
  // mis-configure every other property.
  const [syncedPropertyId, setSyncedPropertyId] = useState<string | null>(null);

  const effectivePropertyId =
    propertyId || (properties && properties.length === 1 ? properties[0].id : "");
  const selectedProperty = properties?.find((p) => p.id === effectivePropertyId) ?? null;

  if (effectivePropertyId !== syncedPropertyId) {
    setSyncedPropertyId(effectivePropertyId || null);
    setForm(selectedProperty ? formFromProperty(selectedProperty) : null);
    setError(null);
  }

  async function handleSave() {
    if (!selectedProperty || !form) return;
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
      await api.properties.update(selectedProperty.id, payload);
      await refetch();
      toast.success("Call ownership saved");
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to save call ownership";
      setError(message);
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  // properties === null (not the `loading` flag) gates the skeleton --
  // `loading` flips true again on every refetch() too (see use-async.ts),
  // and the existing reference components in this codebase (properties/
  // page.tsx, technicians-section.tsx) never gate their main content on it
  // past the initial mount, letting stale data show through during a
  // background refetch instead. Gating on `loading` here caused a real bug
  // during review: the whole card -- including the "saved" state the host
  // should be looking at -- flashed back to a loading skeleton immediately
  // after every successful Save, because handleSave awaits refetch(),
  // which re-sets loading=true for the list call.
  if (properties === null && loading) {
    return (
      <Card>
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
      <Card>
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

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Call ownership</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">Choose when Mira answers guest calls and when you take them yourself.</p>

        {properties.length > 1 && (
          <div className="max-w-sm space-y-2">
            <Label htmlFor="call-ownership-property">Property</Label>
            <PropertyCombobox
              properties={properties}
              value={effectivePropertyId}
              onChange={setPropertyId}
              placeholder="Choose a property to configure"
            />
          </div>
        )}

        {!selectedProperty || !form ? (
          <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">
            Select a property above to configure its call ownership.
          </p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {MODES.map((mode) => (
                <Button
                  key={mode.value}
                  type="button"
                  variant={form.call_handling_mode === mode.value ? "default" : "outline"}
                  size="sm"
                  aria-pressed={form.call_handling_mode === mode.value}
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
              <div className="space-y-4 rounded-lg border bg-muted/50 p-4">
                <div className="grid gap-4 sm:grid-cols-3">
                  <div className="space-y-2">
                    <Label htmlFor="schedule-start">You answer from</Label>
                    <Input
                      id="schedule-start"
                      type="time"
                      required
                      value={form.call_handling_schedule_start}
                      onChange={(e) => setForm({ ...form, call_handling_schedule_start: e.target.value })}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="schedule-end">You answer until</Label>
                    <Input
                      id="schedule-end"
                      type="time"
                      required
                      value={form.call_handling_schedule_end}
                      onChange={(e) => setForm({ ...form, call_handling_schedule_end: e.target.value })}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="schedule-timezone">Timezone</Label>
                    <Select
                      value={form.timezone}
                      onValueChange={(v) => v && setForm({ ...form, timezone: v })}
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
                  These hours apply every day. An overnight window (e.g. 10:00 PM – 6:00 AM) is fine — it
                  stays in effect across midnight.
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
            )}

            {error && <p className="text-sm text-destructive">{error}</p>}

            <div className="flex justify-end">
              <Button type="button" onClick={handleSave} disabled={saving}>
                {saving ? "Saving…" : "Save changes"}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
