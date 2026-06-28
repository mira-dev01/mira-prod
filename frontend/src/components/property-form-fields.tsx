"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { FAQItem, PropertyCreate } from "@/lib/types";

type PropertyFormValue = PropertyCreate;

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
    <div className="space-y-4">
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

      <div className="space-y-2">
        <Label htmlFor="house_rules">House rules</Label>
        <Textarea
          id="house_rules"
          value={form.house_rules ?? ""}
          onChange={(e) => onChange({ ...form, house_rules: e.target.value })}
        />
      </div>

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
