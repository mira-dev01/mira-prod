"use client";

import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { PropertyCreate } from "@/lib/types";

type Form = PropertyCreate;

/**
 * The "Rooms" tab of a property: renting the same property room by room, next
 * to renting it as the entire place (the "Entire place" tab -- the existing
 * price, guest limit and calendar).
 *
 * It is one property either way: one identifier, one calendar. A booking for
 * some rooms and a booking for the entire place can't overlap silently --
 * when a guest asks for the whole place on dates that already have a room
 * booking, Mira tells them there's a temporary hold and raises the double
 * occupancy with you to resolve.
 */
export function PropertyRoomRentalTab({ form, onChange }: { form: Form; onChange: (next: Form) => void }) {
  const enabled = form.room_rental_enabled ?? false;
  const rooms = form.rentable_room_count ?? null;
  const perRoom = form.room_max_guests ?? null;

  function num(value: string): number | null {
    return value === "" ? null : Number(value);
  }

  return (
    <div className="space-y-5">
      <div
        className="flex items-center justify-between gap-3 rounded-lg border p-3"
        onClick={(e) => {
          // Same base-ui Switch click fallback used on the Entire place tab.
          if (e.defaultPrevented || (e.target as HTMLElement).closest("label")) return;
          onChange({ ...form, room_rental_enabled: !enabled });
        }}
      >
        <div className="min-w-0">
          <Label htmlFor="room_rental_enabled">Also rent this property by the room</Label>
          <p className="text-xs text-muted-foreground">
            Guests can book some of the rooms instead of the whole place. For a party or a big group Mira offers the
            entire place; for a night stay for a few people she offers rooms.
          </p>
        </div>
        <Switch
          id="room_rental_enabled"
          className="shrink-0"
          checked={enabled}
          onCheckedChange={(checked) => onChange({ ...form, room_rental_enabled: checked })}
        />
      </div>

      <div className={enabled ? "space-y-5" : "pointer-events-none space-y-5 opacity-50"} aria-disabled={!enabled}>
        <div className="grid grid-cols-3 gap-4">
          <div className="space-y-2">
            <Label htmlFor="room_base_price">Price per room (₹/night)</Label>
            <Input
              id="room_base_price"
              type="number"
              min={0}
              required={enabled}
              value={form.room_base_price ?? ""}
              onChange={(e) => onChange({ ...form, room_base_price: num(e.target.value) })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="rentable_room_count">Rooms that can be rented</Label>
            <Input
              id="rentable_room_count"
              type="number"
              min={1}
              required={enabled}
              value={rooms ?? ""}
              onChange={(e) => onChange({ ...form, rentable_room_count: num(e.target.value) })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="room_max_guests">Guests per room</Label>
            <Input
              id="room_max_guests"
              type="number"
              min={1}
              required={enabled}
              value={perRoom ?? ""}
              onChange={(e) => onChange({ ...form, room_max_guests: num(e.target.value) })}
            />
          </div>
        </div>
        {enabled && rooms && perRoom ? (
          <p className="text-xs text-muted-foreground">
            Rooms alone sleep up to {rooms * perRoom} guests. The entire place ({form.max_guests ?? "—"} guests, ₹
            {form.base_price}/night) is set on the Entire place tab.
          </p>
        ) : null}

        <div className="space-y-2">
          <Label htmlFor="room_details">What a guest gets in a room</Label>
          <DictationTextarea
            id="room_details"
            rows={3}
            placeholder="e.g. King bed, attached bathroom, shared kitchen and living room"
            value={form.room_details ?? ""}
            onValueChange={(value) => onChange({ ...form, room_details: value })}
          />
          <p className="text-xs text-muted-foreground">
            Mira only tells guests what is written here. Leave out anything you don&apos;t want promised.
          </p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="room_ical_url">Room listing calendar (iCal URL, optional)</Label>
          <Input
            id="room_ical_url"
            placeholder="https://www.airbnb.com/calendar/ical/…"
            value={form.room_ical_url ?? ""}
            onChange={(e) => onChange({ ...form, room_ical_url: e.target.value })}
          />
          <p className="text-xs text-muted-foreground">
            If you list the rooms on Airbnb separately, paste that listing&apos;s calendar here. Its bookings show on this
            property&apos;s calendar as room bookings (one room each until you correct the count). The entire-place
            calendar stays on the Entire place tab.
          </p>
        </div>
      </div>
    </div>
  );
}
