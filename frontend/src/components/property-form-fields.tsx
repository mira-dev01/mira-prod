"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { FAQItem, PropertyCreate } from "@/lib/types";

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
  const faq = form.faq ?? [];

  function updateFaqItem(index: number, patch: Partial<FAQItem>) {
    const next = faq.map((item, i) => (i === index ? { ...item, ...patch } : item));
    onChange({ ...form, faq: next });
  }

  function addFaqItem() {
    onChange({ ...form, faq: [...faq, { question: "", answer: "" }] });
  }

  function removeFaqItem(index: number) {
    onChange({ ...form, faq: faq.filter((_, i) => i !== index) });
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
        <Input
          id="usp"
          placeholder="e.g. Glass house, 1BHK with a private jacuzzi"
          maxLength={280}
          value={form.usp ?? ""}
          onChange={(e) => onChange({ ...form, usp: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          MIRA leads with this whenever a guest asks generally about the property, and uses it when
          comparing properties for the Lead Agent.
        </p>
      </div>

      <SectionLabel>Amenities</SectionLabel>
      <div className="space-y-2">
        <Label htmlFor="amenities">Amenities (comma-separated)</Label>
        <Textarea
          id="amenities"
          placeholder="WiFi, AC, Pool, Free parking"
          value={amenitiesText}
          onChange={(e) =>
            onChange({
              ...form,
              amenities: e.target.value
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
        <Textarea
          id="house_rules"
          placeholder="Check-in process, smoking/pets policy, quiet hours, ID requirements..."
          value={form.house_rules ?? ""}
          onChange={(e) => onChange({ ...form, house_rules: e.target.value })}
        />
      </div>

      <SectionLabel>Neighborhood &amp; local area</SectionLabel>
      <div className="space-y-2">
        <Label htmlFor="neighborhood_info">Neighborhood info</Label>
        <Textarea
          id="neighborhood_info"
          placeholder={
            "e.g. 10 min walk to Baga beach. Scooter rentals right outside the gate, ~₹400/day. " +
            "Cafes: Artjuna (5 min walk), Thalassa (10 min). Cabs widely available -- ~₹800 to the " +
            "airport (40 min), ~₹300 to Thivim railway station (15 min)."
          }
          value={form.neighborhood_info ?? ""}
          onChange={(e) => onChange({ ...form, neighborhood_info: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          MIRA answers local-area questions directly from this -- nearby cafes, rentals, distance to
          the beach/airport/railway station, cab availability and typical fares, etc.
        </p>
      </div>

      <SectionLabel>FAQ</SectionLabel>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label>FAQ</Label>
          <Button type="button" variant="outline" size="sm" onClick={addFaqItem}>
            Add question
          </Button>
        </div>
        {faq.length === 0 ? (
          <p className="text-xs text-muted-foreground">No FAQ entries on this property yet.</p>
        ) : (
          <div className="space-y-3">
            {faq.map((item, index) => (
              <div key={index} className="space-y-2 rounded-md border p-3">
                <Input
                  placeholder="Question"
                  value={item.question}
                  onChange={(e) => updateFaqItem(index, { question: e.target.value })}
                />
                <Textarea
                  placeholder="Answer"
                  value={item.answer}
                  onChange={(e) => updateFaqItem(index, { answer: e.target.value })}
                />
                <Button type="button" variant="ghost" size="sm" onClick={() => removeFaqItem(index)}>
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
