"use client";

import { CapabilityNotice } from "@/components/capability-notice";
import { Button } from "@/components/ui/button";
import { DictationInput } from "@/components/ui/dictation-input";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { FieldRow, SubField, SheetHeading, WideCell } from "@/components/ui/sheet";
import { PropertyFormSection } from "@/components/property-form-section";
import { ToggleField } from "@/components/toggle-field";
import type { PropertyCreate, SeasonalNote } from "@/lib/types";

type PropertyFormValue = PropertyCreate;

// Call ownership (who answers, and when) is no longer per-property -- it's
// an account-global window on the Settings page (Host call hours). See
// documentation/host-call-hours-and-handoff.md. The per-property
// call_handling_* columns still exist in the DB but nothing writes them.

export function PropertyFormFields({
  form,
  onChange,
}: {
  form: PropertyFormValue;
  onChange: (next: PropertyFormValue) => void;
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
    <>
      <WideCell>
        <SheetHeading title="Basics" />
        <FieldRow label="Name" htmlFor="name" required>
          <Input id="name" required value={form.name} onChange={(e) => onChange({ ...form, name: e.target.value })} />
        </FieldRow>
        <FieldRow label="City" htmlFor="city">
          <Input id="city" value={form.city ?? ""} onChange={(e) => onChange({ ...form, city: e.target.value })} />
        </FieldRow>
        <FieldRow label="ExoPhone" htmlFor="exophone" hint="The number guests dial for this property.">
          <Input
            id="exophone"
            placeholder="+9180XXXXXXXX"
            value={form.exophone ?? ""}
            onChange={(e) => onChange({ ...form, exophone: e.target.value })}
          />
        </FieldRow>
        <FieldRow label="Premium" hint="Mira recommends it first when a guest wants something more premium.">
          <ToggleField
            id="is_premium"
            label="Premium property"
            checked={form.is_premium ?? false}
            onChange={(checked) => onChange({ ...form, is_premium: checked })}
          />
        </FieldRow>
      </WideCell>

      <WideCell>
        <SheetHeading title="Pricing and stay" />
        <FieldRow label="Price">
          <SubField label="Base price (₹/night)" htmlFor="base_price" className="sm:max-w-56">
            <Input
              id="base_price"
              type="number"
              min={0}
              required
              value={form.base_price}
              onChange={(e) => onChange({ ...form, base_price: Number(e.target.value) })}
            />
          </SubField>
          <CapabilityNotice
            capabilityId="live_airbnb_pricing"
            whenUnavailable="Live Airbnb prices aren't connected for your workspace yet, so Mira quotes the base price above even with Smart pricing on."
          />
          <ToggleField
            id="exact_airbnb_pricing"
            label="Smart pricing"
            hint="Quote the live Airbnb rate for the guest's dates instead. Needs a linked Airbnb listing."
            checked={form.exact_airbnb_pricing ?? false}
            onChange={(checked) => onChange({ ...form, exact_airbnb_pricing: checked })}
          />
        </FieldRow>
        <FieldRow label="Guests and nights">
          <div className="grid gap-4 sm:grid-cols-2">
            <SubField label="Max guests" htmlFor="max_guests">
              <Input
                id="max_guests"
                type="number"
                min={1}
                value={form.max_guests}
                onChange={(e) => onChange({ ...form, max_guests: Number(e.target.value) })}
              />
            </SubField>
            <SubField label="Minimum stay (nights)" htmlFor="minimum_nights">
              <Input
                id="minimum_nights"
                type="number"
                min={1}
                value={form.minimum_nights ?? 1}
                onChange={(e) => onChange({ ...form, minimum_nights: Number(e.target.value) })}
              />
            </SubField>
          </div>
          <ToggleField
            id="saturday_minimum_stay_enabled"
            label="Saturday 2-night minimum"
            checked={form.saturday_minimum_stay_enabled ?? false}
            onChange={(checked) => onChange({ ...form, saturday_minimum_stay_enabled: checked })}
          />
        </FieldRow>
        <FieldRow label="Check-in and check-out">
          <div className="grid gap-4 sm:grid-cols-2">
            <SubField label="Check-in time" htmlFor="check_in_time">
              <Input
                id="check_in_time"
                type="time"
                value={form.check_in_time ?? "14:00"}
                onChange={(e) => onChange({ ...form, check_in_time: e.target.value })}
              />
            </SubField>
            <SubField label="Check-out time" htmlFor="check_out_time">
              <Input
                id="check_out_time"
                type="time"
                value={form.check_out_time ?? "11:00"}
                onChange={(e) => onChange({ ...form, check_out_time: e.target.value })}
              />
            </SubField>
          </div>
        </FieldRow>
        <FieldRow label="Calendar" hint="Syncs bookings from another calendar.">
          <SubField label="iCal URL" htmlFor="ical_url">
            <Input
              id="ical_url"
              value={form.ical_url ?? ""}
              onChange={(e) => onChange({ ...form, ical_url: e.target.value })}
            />
          </SubField>
        </FieldRow>
      </WideCell>

      <WideCell>
        <SheetHeading title="What Mira tells guests" />
        <FieldRow label="Description" htmlFor="usp">
          <DictationInput
            id="usp"
            placeholder="e.g. Glass house, 1BHK with a private jacuzzi"
            maxLength={280}
            value={form.usp ?? ""}
            onValueChange={(value) => onChange({ ...form, usp: value })}
          />
        </FieldRow>
        <FieldRow label="Amenities" htmlFor="amenities" hint="Comma-separated.">
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
        </FieldRow>
        <FieldRow label="House rules" htmlFor="house_rules">
          <DictationTextarea
            id="house_rules"
            placeholder="Check-in process, smoking/pets policy, quiet hours, ID requirements..."
            value={form.house_rules ?? ""}
            onValueChange={(value) => onChange({ ...form, house_rules: value })}
          />
        </FieldRow>
        <FieldRow label="Neighborhood" htmlFor="neighborhood_info">
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
        </FieldRow>
      </WideCell>

      <PropertyFormSection
        title="Seasonal notes"
        count={seasonalNotes.length}
        action={
          <Button type="button" variant="outline" size="sm" onClick={addSeasonalNote}>
            + Add note
          </Button>
        }
        helpText="Only mentioned in the months they apply. A range can wrap the year, e.g. 11 to 2."
      >
        {seasonalNotes.length === 0 ? (
          <p className="p-5 text-sm text-muted-foreground">No seasonal notes yet.</p>
        ) : (
          <div className="px-5">
            {seasonalNotes.map((item, index) => (
              <div key={index} className="space-y-3 border-b border-border py-4 last:border-0">
                <DictationTextarea
                  placeholder="e.g. Pool closed for monsoon cleaning."
                  value={item.note}
                  onValueChange={(value) => updateSeasonalNote(index, { note: value })}
                />
                <div className="flex flex-wrap items-end gap-4">
                  <SubField label="Start month (1–12)" htmlFor={`seasonal-start-${index}`} className="w-36">
                    <Input
                      id={`seasonal-start-${index}`}
                      type="number"
                      min={1}
                      max={12}
                      value={item.start_month}
                      onChange={(e) => updateSeasonalNote(index, { start_month: Number(e.target.value) })}
                    />
                  </SubField>
                  <SubField label="End month (1–12)" htmlFor={`seasonal-end-${index}`} className="w-36">
                    <Input
                      id={`seasonal-end-${index}`}
                      type="number"
                      min={1}
                      max={12}
                      value={item.end_month}
                      onChange={(e) => updateSeasonalNote(index, { end_month: Number(e.target.value) })}
                    />
                  </SubField>
                  <Button type="button" variant="ghost" size="sm" className="ml-auto text-destructive" onClick={() => removeSeasonalNote(index)}>
                    Remove
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </PropertyFormSection>
    </>
  );
}
