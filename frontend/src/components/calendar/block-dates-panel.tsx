"use client";

import { useState } from "react";
import { toast } from "sonner";
import { GuestCombobox } from "@/components/guest-combobox";
import { Input } from "@/components/ui/input";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { FieldRow, Hint, Sheet, SubField, WideCell } from "@/components/ui/sheet";
import { api, ApiError } from "@/lib/api";
import type { GuestProfileOut, PropertyOut, StayScope } from "@/lib/types";

/** What a request to block dates starts from. `nonce` changes on every open so the form starts fresh. */
export type BlockRequest = { nonce: number; open: boolean; propertyId: string; checkIn: string; checkOut: string };

/**
 * Block dates / add a manual booking. The form's fields live in `BlockDatesForm`, which is re-created
 * (via `key={request.nonce}`) for each open, so every open starts from the date the host clicked.
 */
export function BlockDatesPanel({
  request,
  properties,
  guests,
  onOpenChange,
  onCreated,
  lockProperty = false,
}: {
  request: BlockRequest;
  properties: PropertyOut[];
  guests: GuestProfileOut[];
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
  /** Hide the property picker (the per-property calendar already knows which property). */
  lockProperty?: boolean;
}) {
  // The footer button sits outside the form (RightPanel's footer) and submits it by id.
  const [submitting, setSubmitting] = useState(false);
  return (
    <RightPanel
      open={request.open}
      onOpenChange={onOpenChange}
      title="Block dates"
      footer={
        <RightPanelFooterButton type="submit" form="block-dates-form" disabled={submitting}>
          {submitting ? "Blocking…" : "Block dates"}
        </RightPanelFooterButton>
      }
    >
      <BlockDatesForm
        key={request.nonce}
        request={request}
        properties={properties}
        guests={guests}
        lockProperty={lockProperty}
        onSubmitting={setSubmitting}
        onDone={() => {
          onOpenChange(false);
          onCreated();
        }}
      />
    </RightPanel>
  );
}

function BlockDatesForm({
  request,
  properties,
  guests,
  lockProperty,
  onSubmitting,
  onDone,
}: {
  request: BlockRequest;
  properties: PropertyOut[];
  guests: GuestProfileOut[];
  lockProperty: boolean;
  onSubmitting: (submitting: boolean) => void;
  onDone: () => void;
}) {
  const [propertyId, setPropertyId] = useState(request.propertyId || properties[0]?.id || "");
  const [checkIn, setCheckIn] = useState(request.checkIn);
  const [checkOut, setCheckOut] = useState(request.checkOut);
  const [guestName, setGuestName] = useState("");
  const [guestPhone, setGuestPhone] = useState<string | null>(null);
  const [price, setPrice] = useState("");
  const [kind, setKind] = useState<"reservation" | "blocked">("reservation");
  // Only offered for a property that is also rented by the room.
  const [scope, setScope] = useState<StayScope>("entire_place");
  const [rooms, setRooms] = useState("1");

  const offersRooms = !!properties.find((p) => p.id === propertyId)?.room_rental_enabled;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!propertyId || !checkIn || !checkOut) return;
    onSubmitting(true);
    try {
      await api.bookings.create({
        property_id: propertyId,
        check_in: checkIn,
        check_out: checkOut,
        guest_name: guestName || null,
        guest_phone: guestPhone,
        platform: "manual",
        kind,
        stay_scope: offersRooms ? scope : "entire_place",
        rooms_booked: offersRooms && scope === "rooms" ? Number(rooms) : null,
        final_booking_price: kind === "reservation" && price ? Number(price) : null,
      });
      toast.success("Dates blocked");
      onDone();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to block dates");
    } finally {
      onSubmitting(false);
    }
  }

  return (
    <form id="block-dates-form" onSubmit={handleSubmit}>
      <Sheet>
        <WideCell>
          {!lockProperty && (
            <FieldRow label="Property" htmlFor="block-property">
              <Select value={propertyId} onValueChange={(v) => v && setPropertyId(v)}>
                <SelectTrigger id="block-property" className="w-full">
                  <SelectValue placeholder="Select a property">
                    {(value: string) => properties.find((p) => p.id === value)?.name ?? "Select a property"}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {properties.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldRow>
          )}
          <FieldRow label="Dates">
            <div className="grid gap-4 sm:grid-cols-2">
              <SubField label="Check-in" htmlFor="block-check-in">
                <Input id="block-check-in" type="date" required value={checkIn} onChange={(e) => setCheckIn(e.target.value)} />
              </SubField>
              <SubField label="Check-out" htmlFor="block-check-out">
                <Input id="block-check-out" type="date" required value={checkOut} onChange={(e) => setCheckOut(e.target.value)} />
              </SubField>
            </div>
          </FieldRow>
          <FieldRow label="Type" htmlFor="block-kind">
            <Select value={kind} onValueChange={(v) => v && setKind(v as "reservation" | "blocked")}>
              <SelectTrigger id="block-kind" className="w-full">
                <SelectValue>
                  {(value: string) => (value === "blocked" ? "Blocked — no guest (owner use, maintenance)" : "A guest booking")}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="reservation">A guest booking</SelectItem>
                <SelectItem value="blocked">Blocked — no guest (owner use, maintenance)</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>
          <FieldRow label="Guest" hint="Optional.">
            <GuestCombobox
              guests={guests}
              name={guestName}
              onNameChange={setGuestName}
              onSelectGuest={(guest) => setGuestPhone(guest?.phone ?? null)}
            />
            {guestPhone && <Hint>Linked to existing guest ({guestPhone}). This stay will show on their guest profile.</Hint>}
          </FieldRow>
          {offersRooms && (
            <FieldRow
              label="Booking covers"
              htmlFor="block-scope"
              hint="Rooms leave the rest of the property bookable; the entire place closes all of it."
            >
              <div className="flex items-center gap-2">
                <Select value={scope} onValueChange={(v) => v && setScope(v as StayScope)}>
                  <SelectTrigger id="block-scope" className="w-full">
                    <SelectValue>{(value: string) => (value === "rooms" ? "Some of the rooms" : "The entire place")}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="entire_place">The entire place</SelectItem>
                    <SelectItem value="rooms">Some of the rooms</SelectItem>
                  </SelectContent>
                </Select>
                {scope === "rooms" && (
                  <Input
                    type="number"
                    min={1}
                    className="w-24"
                    aria-label="Number of rooms"
                    value={rooms}
                    onChange={(e) => setRooms(e.target.value)}
                  />
                )}
              </div>
            </FieldRow>
          )}
          {kind === "reservation" && (
            <FieldRow label="Final price" htmlFor="block-price" hint="Optional. Total stay in ₹.">
              <Input
                id="block-price"
                type="number"
                inputMode="numeric"
                min={1}
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="Leave empty if not decided yet"
              />
            </FieldRow>
          )}
        </WideCell>
      </Sheet>
    </form>
  );
}
