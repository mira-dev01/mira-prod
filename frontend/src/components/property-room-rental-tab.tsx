"use client";

import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { FieldRow, Sheet, SheetHeading, SubField, WideCell } from "@/components/ui/sheet";
import { ToggleField } from "@/components/toggle-field";
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
    <Sheet>
      <WideCell>
        <SheetHeading title="Rent by the room" />
        <FieldRow label="Rooms">
          <ToggleField
            id="room_rental_enabled"
            label="Also rent this property by the room"
            hint="Guests can book some rooms instead of the whole place."
            checked={enabled}
            onChange={(checked) => onChange({ ...form, room_rental_enabled: checked })}
          />
        </FieldRow>

        <div
          className={enabled ? "col-span-12 grid grid-cols-subgrid" : "pointer-events-none col-span-12 grid grid-cols-subgrid opacity-50"}
          aria-disabled={!enabled}
        >
          <FieldRow label="Price and capacity" hint={enabled && rooms && perRoom ? `Rooms alone sleep up to ${rooms * perRoom} guests.` : undefined}>
            <div className="grid items-end gap-4 sm:grid-cols-3">
              <SubField label="Price per room (₹/night)" htmlFor="room_base_price">
                <Input
                  id="room_base_price"
                  type="number"
                  min={0}
                  required={enabled}
                  value={form.room_base_price ?? ""}
                  onChange={(e) => onChange({ ...form, room_base_price: num(e.target.value) })}
                />
              </SubField>
              <SubField label="Rooms that can be rented" htmlFor="rentable_room_count">
                <Input
                  id="rentable_room_count"
                  type="number"
                  min={1}
                  required={enabled}
                  value={rooms ?? ""}
                  onChange={(e) => onChange({ ...form, rentable_room_count: num(e.target.value) })}
                />
              </SubField>
              <SubField label="Guests per room" htmlFor="room_max_guests">
                <Input
                  id="room_max_guests"
                  type="number"
                  min={1}
                  required={enabled}
                  value={perRoom ?? ""}
                  onChange={(e) => onChange({ ...form, room_max_guests: num(e.target.value) })}
                />
              </SubField>
            </div>
          </FieldRow>

          <FieldRow label="What a guest gets" htmlFor="room_details" hint="Mira only tells guests what is written here.">
            <DictationTextarea
              id="room_details"
              rows={3}
              placeholder="e.g. King bed, attached bathroom, shared kitchen and living room"
              value={form.room_details ?? ""}
              onValueChange={(value) => onChange({ ...form, room_details: value })}
            />
          </FieldRow>

          <FieldRow
            label="Room calendar"
            htmlFor="room_ical_url"
            hint="Optional. iCal URL of your separate room listing."
          >
            <Input
              id="room_ical_url"
              placeholder="https://www.airbnb.com/calendar/ical/…"
              value={form.room_ical_url ?? ""}
              onChange={(e) => onChange({ ...form, room_ical_url: e.target.value })}
            />
          </FieldRow>
        </div>
      </WideCell>
    </Sheet>
  );
}
