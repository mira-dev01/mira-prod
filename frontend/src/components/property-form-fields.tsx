"use client";

import { Button } from "@/components/ui/button";
import { DictationInput } from "@/components/ui/dictation-input";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { PropertyCreate, SeasonalNote } from "@/lib/types";

type PropertyFormValue = PropertyCreate;

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <p className="text-micro pt-2 text-muted-foreground">{children}</p>;
}

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

      <SectionLabel>Description (USP)</SectionLabel>
      <div className="space-y-2">
        <Label htmlFor="usp">One-line description</Label>
        <DictationInput
          id="usp"
          placeholder="e.g. Glass house, 1BHK with a private jacuzzi"
          maxLength={280}
          value={form.usp ?? ""}
          onValueChange={(value) => onChange({ ...form, usp: value })}
        />
        <p className="text-xs text-muted-foreground">
          MIRA leads with this whenever a guest asks generally about the property, and uses it when
          comparing properties for the Lead Agent.
        </p>
      </div>

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

      <SectionLabel>Seasonal notes</SectionLabel>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label>Seasonal notes</Label>
          <Button type="button" variant="outline" size="sm" onClick={addSeasonalNote}>
            Add note
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Time-varying facts MIRA should only mention during the months they apply -- e.g. &quot;pool closed for
          monsoon cleaning&quot; (Jun-Aug) or &quot;extra heater provided&quot; (Nov-Feb, wrapping the year is fine).
        </p>
        {seasonalNotes.length === 0 ? (
          <p className="text-xs text-muted-foreground">No seasonal notes on this property yet.</p>
        ) : (
          <div className="space-y-3">
            {seasonalNotes.map((item, index) => (
              <div key={index} className="space-y-2 rounded-md border p-3">
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
                <Button type="button" variant="ghost" size="sm" onClick={() => removeSeasonalNote(index)}>
                  Remove
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
