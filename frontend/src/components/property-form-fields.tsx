"use client";

import { Button } from "@/components/ui/button";
import { DictationInput } from "@/components/ui/dictation-input";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { PropertyFormSection } from "@/components/property-form-section";
import { cn } from "@/lib/utils";
import type { CallHandlingMode, PropertyCreate, SeasonalNote } from "@/lib/types";

type PropertyFormValue = PropertyCreate;

// Small, deliberately non-exhaustive list -- not every IANA zone, just
// every region an actual host is plausible in today (India-only fleet per
// CLAUDE.md, plus common Airbnb host regions) so the picker is a short,
// scrollable list rather than Intl.supportedValuesOf("timeZone")'s several
// hundred entries. Backend validation (app/schemas/property.py's
// _check_timezone) is authoritative and accepts any real IANA identifier
// regardless of what's offered here -- this list is a UI convenience, not
// a source of truth.
const CALL_HANDLING_TIMEZONES = [
  { value: "Asia/Kolkata", label: "India (Asia/Kolkata)" },
  { value: "Asia/Dubai", label: "UAE (Asia/Dubai)" },
  { value: "Asia/Kathmandu", label: "Nepal (Asia/Kathmandu)" },
  { value: "Asia/Colombo", label: "Sri Lanka (Asia/Colombo)" },
  { value: "Europe/London", label: "United Kingdom (Europe/London)" },
  { value: "America/New_York", label: "US Eastern (America/New_York)" },
  { value: "America/Los_Angeles", label: "US Pacific (America/Los_Angeles)" },
  { value: "Asia/Singapore", label: "Singapore (Asia/Singapore)" },
  { value: "Australia/Sydney", label: "Australia (Australia/Sydney)" },
  { value: "UTC", label: "UTC" },
] as const;

const CALL_HANDLING_MODES: { value: CallHandlingMode; label: string; description: string }[] = [
  { value: "MIRA", label: "Mira", description: "Mira handles all guest calls." },
  { value: "HOST", label: "Host", description: "You handle all guest calls." },
  {
    value: "SCHEDULED",
    label: "Scheduled",
    description: "You handle calls during your configured host hours. Mira handles calls outside those hours.",
  },
];

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <p className="text-micro pt-2 text-muted-foreground">{children}</p>;
}

export function PropertyFormFields({
  form,
  onChange,
  showCallHandling = true,
}: {
  form: PropertyFormValue;
  onChange: (next: PropertyFormValue) => void;
  // Hidden on the "Add property" panel -- the backend's PropertyCreate
  // schema (app/schemas/property.py) deliberately has no
  // call_handling_mode/schedule/timezone fields (Phase 1: a schedule
  // shouldn't be required at creation time), so anything set here during
  // creation would silently be dropped by the API with no error and no
  // indication to the host. Rather than teaching PropertyCreate to accept
  // fields it's designed not to, Call Handling is only editable once the
  // property exists (the Edit panel, which posts through PropertyUpdate
  // and does support these fields) -- same shape as amenities/FAQ/photos,
  // which are also edit-only, not part of the create flow.
  showCallHandling?: boolean;
}) {
  const amenitiesText = (form.amenities ?? []).join(", ");
  const seasonalNotes = form.seasonal_notes ?? [];

  function updateSeasonalNote(index: number, patch: Partial<SeasonalNote>) {
    const next = seasonalNotes.map((item, i) => (i === index ? { ...item, ...patch } : item));
    onChange({ ...form, seasonal_notes: next });
  }

  function addSeasonalNote() {
    onChange({ ...form, seasonal_notes: [...seasonalNotes, { note: "", start_month: 1, end_month: 1 }] });
  }

  function removeSeasonalNote(index: number) {
    onChange({ ...form, seasonal_notes: seasonalNotes.filter((_, i) => i !== index) });
  }

  return (
    <div className="space-y-5">
      <SectionLabel>Basic info</SectionLabel>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="name">Name</Label>
          <Input id="name" required value={form.name} onChange={(e) => onChange({ ...form, name: e.target.value })} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="city">City</Label>
          <Input id="city" value={form.city ?? ""} onChange={(e) => onChange({ ...form, city: e.target.value })} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="exophone">ExoPhone</Label>
          <Input
            id="exophone"
            placeholder="+9180XXXXXXXX"
            value={form.exophone ?? ""}
            onChange={(e) => onChange({ ...form, exophone: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="base_price">Base price (₹/night)</Label>
          <Input
            id="base_price"
            type="number"
            min={0}
            required
            value={form.base_price}
            onChange={(e) => onChange({ ...form, base_price: Number(e.target.value) })}
          />
        </div>
        <div
          className="col-span-2 flex items-center justify-between rounded-lg border p-3"
          onClick={(e) => {
            // Fallback for a confirmed-live bug: a real click directly on the
            // switch pill sometimes never reaches its own click handler (a
            // synthetic .click() on the same element always works, so this
            // isn't a logic bug in the switch itself -- more likely a
            // hit-testing/positioning quirk). Base-ui's Switch calls
            // preventDefault() the instant its own onClick runs, and a native
            // <label for> click already toggles the input directly -- skip
            // this fallback in both of those cases so a click that already
            // worked never gets double-toggled back to its original state.
            if (e.defaultPrevented || (e.target as HTMLElement).closest("label")) return;
            onChange({ ...form, exact_airbnb_pricing: !(form.exact_airbnb_pricing ?? false) });
          }}
        >
          <div className="space-y-0.5">
            <Label htmlFor="exact_airbnb_pricing">Quote live Airbnb Smart Pricing</Label>
            <p className="text-micro text-muted-foreground">
              Off: Mira quotes the base price above as-is (no markup either way). On: Mira looks up this listing's
              live price on Airbnb for the guest's actual dates instead — turn this on if you use Airbnb Smart
              Pricing and want Mira to always match today's real rate. Requires an Airbnb listing to be linked.
            </p>
          </div>
          <Switch
            id="exact_airbnb_pricing"
            checked={form.exact_airbnb_pricing ?? false}
            onCheckedChange={(checked) => onChange({ ...form, exact_airbnb_pricing: checked })}
          />
        </div>
        <div
          className="col-span-2 flex items-center justify-between rounded-lg border p-3"
          onClick={(e) => {
            if (e.defaultPrevented || (e.target as HTMLElement).closest("label")) return;
            onChange({ ...form, is_premium: !(form.is_premium ?? false) });
          }}
        >
          <div className="space-y-0.5">
            <Label htmlFor="is_premium">Premium property</Label>
            <p className="text-micro text-muted-foreground">
              Mark this as one of your nicer/higher-end properties — Mira uses this to recommend it first when a
              guest asks for something more premium.
            </p>
          </div>
          <Switch
            id="is_premium"
            checked={form.is_premium ?? false}
            onCheckedChange={(checked) => onChange({ ...form, is_premium: checked })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="max_guests">Max guests</Label>
          <Input
            id="max_guests"
            type="number"
            min={1}
            value={form.max_guests}
            onChange={(e) => onChange({ ...form, max_guests: Number(e.target.value) })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="minimum_nights">Minimum stay (nights)</Label>
          <Input
            id="minimum_nights"
            type="number"
            min={1}
            value={form.minimum_nights ?? 1}
            onChange={(e) => onChange({ ...form, minimum_nights: Number(e.target.value) })}
          />
        </div>
        <div
          className="col-span-2 flex items-center justify-between rounded-lg border p-3"
          onClick={(e) => {
            // Same base-ui Switch click fallback as exact_airbnb_pricing above.
            if (e.defaultPrevented || (e.target as HTMLElement).closest("label")) return;
            onChange({ ...form, saturday_minimum_stay_enabled: !(form.saturday_minimum_stay_enabled ?? false) });
          }}
        >
          <div className="space-y-0.5">
            <Label htmlFor="saturday_minimum_stay_enabled">Require 2-night minimum on Saturdays</Label>
            <p className="text-micro text-muted-foreground">
              Off: a guest can book a single Saturday night on its own. On: any stay that includes a Saturday night
              must be at least 2 nights — a lone Saturday-only booking is turned down. Independent of the minimum
              stay above.
            </p>
          </div>
          <Switch
            id="saturday_minimum_stay_enabled"
            checked={form.saturday_minimum_stay_enabled ?? false}
            onCheckedChange={(checked) => onChange({ ...form, saturday_minimum_stay_enabled: checked })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ical_url">iCal URL</Label>
          <Input
            id="ical_url"
            value={form.ical_url ?? ""}
            onChange={(e) => onChange({ ...form, ical_url: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="check_in_time">Check-in time</Label>
          <Input
            id="check_in_time"
            type="time"
            value={form.check_in_time ?? "14:00"}
            onChange={(e) => onChange({ ...form, check_in_time: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="check_out_time">Check-out time</Label>
          <Input
            id="check_out_time"
            type="time"
            value={form.check_out_time ?? "11:00"}
            onChange={(e) => onChange({ ...form, check_out_time: e.target.value })}
          />
        </div>
      </div>

      {showCallHandling && (
        <PropertyFormSection
          icon="C"
          title="Call Handling"
          helpText="Who answers guest calls to this property, and when."
        >
          <div className="space-y-4">
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2">
                {CALL_HANDLING_MODES.map((mode) => (
                  <Button
                    key={mode.value}
                    type="button"
                    variant={(form.call_handling_mode ?? "MIRA") === mode.value ? "default" : "outline"}
                    size="sm"
                    aria-pressed={(form.call_handling_mode ?? "MIRA") === mode.value}
                    className={cn((form.call_handling_mode ?? "MIRA") === mode.value && "pointer-events-none")}
                    onClick={() => onChange({ ...form, call_handling_mode: mode.value })}
                  >
                    {mode.label}
                  </Button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                {CALL_HANDLING_MODES.find((m) => m.value === (form.call_handling_mode ?? "MIRA"))?.description}
              </p>
            </div>

            {form.call_handling_mode === "SCHEDULED" && (
              <div className="grid grid-cols-2 gap-4 rounded-lg border bg-muted/50 p-3">
                <div className="space-y-2">
                  <Label htmlFor="call_handling_schedule_start">Host hours start</Label>
                  <Input
                    id="call_handling_schedule_start"
                    type="time"
                    required
                    value={form.call_handling_schedule_start ?? ""}
                    onChange={(e) => onChange({ ...form, call_handling_schedule_start: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="call_handling_schedule_end">Host hours end</Label>
                  <Input
                    id="call_handling_schedule_end"
                    type="time"
                    required
                    value={form.call_handling_schedule_end ?? ""}
                    onChange={(e) => onChange({ ...form, call_handling_schedule_end: e.target.value })}
                  />
                </div>
                <div className="col-span-2 space-y-2">
                  <Label htmlFor="call_handling_timezone">Timezone</Label>
                  <Select
                    value={form.timezone ?? "Asia/Kolkata"}
                    onValueChange={(v) => v && onChange({ ...form, timezone: v })}
                  >
                    <SelectTrigger id="call_handling_timezone" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CALL_HANDLING_TIMEZONES.map((tz) => (
                        <SelectItem key={tz.value} value={tz.value}>
                          {tz.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <p className="col-span-2 text-xs text-muted-foreground">
                  An overnight window (e.g. 22:00 → 06:00) is valid -- host hours span past midnight.
                </p>
              </div>
            )}
          </div>
        </PropertyFormSection>
      )}

      <PropertyFormSection
        icon="D"
        title="Description"
        helpText="MIRA answers general questions about the property directly from this -- what makes it stand out, the vibe, what's nearby."
      >
        <div className="space-y-2 rounded-lg border bg-muted/50 p-3">
          <Label htmlFor="usp">One-line description</Label>
          <DictationInput
            id="usp"
            placeholder="e.g. Glass house, 1BHK with a private jacuzzi"
            maxLength={280}
            value={form.usp ?? ""}
            onValueChange={(value) => onChange({ ...form, usp: value })}
          />
        </div>
      </PropertyFormSection>

      <SectionLabel>Amenities</SectionLabel>
      <div className="space-y-2">
        <Label htmlFor="amenities">Amenities (comma-separated)</Label>
        <DictationTextarea
          id="amenities"
          placeholder="WiFi, AC, Pool, Free parking"
          value={amenitiesText}
          onValueChange={(value) =>
            onChange({
              ...form,
              amenities: value
                .split(",")
                .map((item) => item.trim())
                .filter(Boolean),
            })
          }
        />
      </div>

      <SectionLabel>House rules</SectionLabel>
      <div className="space-y-2">
        <Label htmlFor="house_rules">House rules</Label>
        <DictationTextarea
          id="house_rules"
          placeholder="Check-in process, smoking/pets policy, quiet hours, ID requirements..."
          value={form.house_rules ?? ""}
          onValueChange={(value) => onChange({ ...form, house_rules: value })}
        />
      </div>

      <SectionLabel>Neighborhood &amp; local area</SectionLabel>
      <div className="space-y-2">
        <Label htmlFor="neighborhood_info">Neighborhood info</Label>
        <DictationTextarea
          id="neighborhood_info"
          placeholder={
            "e.g. 10 min walk to Baga beach. Scooter rentals right outside the gate, ~₹400/day. " +
            "Cafes: Artjuna (5 min walk), Thalassa (10 min). Cabs widely available -- ~₹800 to the " +
            "airport (40 min), ~₹300 to Thivim railway station (15 min)."
          }
          value={form.neighborhood_info ?? ""}
          onValueChange={(value) => onChange({ ...form, neighborhood_info: value })}
        />
        <p className="text-xs text-muted-foreground">
          MIRA answers local-area questions directly from this -- nearby cafes, rentals, distance to
          the beach/airport/railway station, cab availability and typical fares, etc.
        </p>
      </div>

      <PropertyFormSection
        icon="S"
        title="Seasonal Notes"
        count={seasonalNotes.length}
        action={
          <Button type="button" variant="outline" size="sm" onClick={addSeasonalNote}>
            + Add note
          </Button>
        }
        helpText={
          <>
            Time-varying facts MIRA should only mention during the months they apply -- e.g. &quot;pool closed for
            monsoon cleaning&quot; (Jun-Aug) or &quot;extra heater provided&quot; (Nov-Feb, wrapping the year is fine).
          </>
        }
      >
        {seasonalNotes.length === 0 ? (
          <p className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">
            No seasonal notes on this property yet.
          </p>
        ) : (
          <div className="space-y-3">
            {seasonalNotes.map((item, index) => (
              <div key={index} className="space-y-2 rounded-lg border p-3">
                <DictationTextarea
                  placeholder="e.g. Pool closed for monsoon cleaning."
                  value={item.note}
                  onValueChange={(value) => updateSeasonalNote(index, { note: value })}
                />
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label htmlFor={`seasonal-start-${index}`}>Start month</Label>
                    <Input
                      id={`seasonal-start-${index}`}
                      type="number"
                      min={1}
                      max={12}
                      value={item.start_month}
                      onChange={(e) => updateSeasonalNote(index, { start_month: Number(e.target.value) })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`seasonal-end-${index}`}>End month</Label>
                    <Input
                      id={`seasonal-end-${index}`}
                      type="number"
                      min={1}
                      max={12}
                      value={item.end_month}
                      onChange={(e) => updateSeasonalNote(index, { end_month: Number(e.target.value) })}
                    />
                  </div>
                </div>
                <Button type="button" variant="ghost" size="sm" className="text-destructive" onClick={() => removeSeasonalNote(index)}>
                  Remove
                </Button>
              </div>
            ))}
          </div>
        )}
      </PropertyFormSection>
    </div>
  );
}
