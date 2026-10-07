"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, MessageSquare, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RightPanel } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip } from "@/components/status-chip";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { formatINR, formatStayDates, nightsBetween } from "@/lib/format";
import { leadTemperatureLabel, leadTemperatureTone } from "@/lib/leads";
import { cn } from "@/lib/utils";
import type { BookingReconciliation, PriceChoice } from "@/lib/types";

/**
 * Host-facing answers to the two questions only the host can settle for a
 * booking (backend/app/services/booking_reconciliation_service.py):
 *   - "Mira match?"          -> PATCH /bookings/{id}/attribution
 *   - "Final booking price"  -> PATCH /bookings/{id}/price
 * Shared by the Calendar's booking panel and the Analytics page's
 * Needs-confirmation list, so the flow is identical wherever it's opened.
 */

const SIGNAL_LABELS: Record<string, string> = {
  same_property: "Same property",
  exact_dates: "Same dates",
  overlapping_dates: "Overlapping dates",
  phone_match: "Same phone number",
  phone_last4_match: "Phone ends in the same 4 digits",
  hot_intent: "Strong booking intent",
  booking_intent: "Said they wanted to book",
  pricing_discussed: "Mira quoted a price",
  negotiation: "Negotiated with Mira",
  recent: "Spoke to Mira recently",
  host_closed_lead: "Confirmed from Live Requests",
};

const PRICE_SOURCE_LABELS: Record<string, string> = {
  mira_conversation: "From the Mira conversation",
  host_confirmed: "Confirmed by you from the Mira conversation",
  host_entered: "Entered by you",
  external_pms: "From your PMS",
};

function guestLabel(item: BookingReconciliation): string {
  const { booking, conversation } = item;
  if (booking.guest_phone) return booking.guest_phone;
  if (booking.guest_phone_last4) return `Phone ending ${booking.guest_phone_last4}`;
  if (conversation?.guest_phone) return conversation.guest_phone;
  return "Guest details not shared";
}

/** Booking header: property, dates, guest, conversation found or not. */
export function BookingSummary({ item }: { item: BookingReconciliation }) {
  const { booking, conversation } = item;
  const nights = nightsBetween(booking.check_in, booking.check_out);
  return (
    <div className="space-y-1 rounded-lg border p-3 text-sm">
      <p className="font-medium">{item.property_name}</p>
      <p className="text-muted-foreground">
        {formatStayDates(booking.check_in, booking.check_out)} · {nights} night{nights === 1 ? "" : "s"}
        <span className="capitalize"> · {booking.platform}</span>
      </p>
      <p className="text-muted-foreground">Guest: {guestLabel(item)}</p>
      <p className="text-muted-foreground">Mira conversation: {conversation ? "Found" : "Not found"}</p>
    </div>
  );
}

function ConversationCard({ item }: { item: BookingReconciliation }) {
  const { conversation, booking } = item;
  if (!conversation) return null;
  const signals = booking.mira_attribution_signals.filter((s) => SIGNAL_LABELS[s]);
  return (
    <div className="space-y-2 rounded-lg border bg-muted/30 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <MessageSquare className="size-4 text-muted-foreground" />
        <span className="font-medium">{conversation.guest_name ?? conversation.guest_phone ?? "Guest"}</span>
        {conversation.lead_temperature && (
          <StatusChip
            status={leadTemperatureLabel(conversation.lead_temperature)}
            tone={leadTemperatureTone[conversation.lead_temperature] ?? "neutral"}
          />
        )}
        {conversation.conversation_at && (
          <span className="text-xs text-muted-foreground">
            {new Date(conversation.conversation_at).toLocaleString("en-IN", {
              day: "numeric",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        )}
      </div>
      {conversation.summary && <p className="text-muted-foreground">{conversation.summary}</p>}
      {signals.length > 0 && (
        <p className="text-xs text-muted-foreground">Why we think so: {signals.map((s) => SIGNAL_LABELS[s]).join(" · ")}</p>
      )}
      {conversation.call_session_id && (
        <Link
          href={`/dashboard/calls/${conversation.call_session_id}`}
          className="text-xs font-medium text-foreground underline-offset-4 hover:underline"
        >
          Open the call
        </Link>
      )}
    </div>
  );
}

/** "Mira match?" Yes/No -- only rendered when there's a real candidate. */
export function MiraMatchControl({
  item,
  onDecided,
  compact,
}: {
  item: BookingReconciliation;
  onDecided: (updated: BookingReconciliation) => void;
  compact?: boolean;
}) {
  const [saving, setSaving] = useState<boolean | null>(null);
  const { booking } = item;
  if (!booking.mira_attribution_lead_id) return null;

  async function decide(isMatch: boolean) {
    setSaving(isMatch);
    try {
      const updated = await api.bookings.decideAttribution(booking.id, isMatch);
      toast.success(isMatch ? "Marked as a Mira booking" : "Marked as not a Mira booking");
      onDecided(updated);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't save that");
    } finally {
      setSaving(null);
    }
  }

  const status = booking.mira_attribution_status;
  return (
    <div className={cn("flex flex-wrap items-center gap-2", !compact && "justify-between")}>
      <span className="text-sm font-medium">Mira match?</span>
      <div className="flex gap-2" role="group" aria-label="Was this booking from a Mira conversation?">
        <Button
          size="sm"
          variant={status === "confirmed" ? "default" : "outline"}
          aria-pressed={status === "confirmed"}
          disabled={saving !== null}
          onClick={() => decide(true)}
        >
          <Check className="size-3.5" /> Yes
        </Button>
        <Button
          size="sm"
          variant={status === "not_attributed" ? "default" : "outline"}
          aria-pressed={status === "not_attributed"}
          disabled={saving !== null}
          onClick={() => decide(false)}
        >
          <X className="size-3.5" /> No
        </Button>
      </div>
    </div>
  );
}

/** The three-way final-price question. */
export function FinalPriceForm({
  item,
  onSaved,
}: {
  item: BookingReconciliation;
  onSaved: (updated: BookingReconciliation) => void;
}) {
  const { booking } = item;
  const detected = booking.detected_price;
  const [choice, setChoice] = useState<PriceChoice>(detected !== null ? "mira_conversation" : "outside_mira");
  const [amount, setAmount] = useState<string>(detected !== null ? String(Math.round(detected)) : "");
  const [saving, setSaving] = useState(false);

  const options: { value: PriceChoice; title: string; description: string; disabled?: boolean }[] = [
    {
      value: "mira_conversation",
      title: "Price confirmed during Mira conversation",
      description:
        detected !== null ? `Mira's conversation ended at ${formatINR(detected)}.` : "No price found in a Mira conversation.",
      disabled: detected === null,
    },
    { value: "outside_mira", title: "Price was finalized outside Mira", description: "Enter what the guest paid." },
    { value: "not_confirmed_yet", title: "Price isn't confirmed yet", description: "We'll leave it pending." },
  ];

  async function save() {
    const parsed = Number(amount);
    if (choice !== "not_confirmed_yet" && (!amount || !Number.isFinite(parsed) || parsed <= 0)) {
      toast.error("Enter the final booking price");
      return;
    }
    setSaving(true);
    try {
      const updated = await api.bookings.confirmPrice(booking.id, {
        choice,
        final_booking_price: choice === "not_confirmed_yet" ? null : parsed,
      });
      toast.success(choice === "not_confirmed_yet" ? "Left as pending" : "Booking price saved");
      onSaved(updated);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't save the price");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      <div role="radiogroup" aria-label="Final booking price" className="space-y-2">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={choice === option.value}
            disabled={option.disabled}
            onClick={() => {
              setChoice(option.value);
              if (option.value === "mira_conversation" && detected !== null) setAmount(String(Math.round(detected)));
            }}
            className={cn(
              "flex w-full items-start gap-3 rounded-lg border p-3 text-left text-sm transition-colors",
              "disabled:cursor-not-allowed disabled:opacity-50",
              choice === option.value ? "border-foreground/40 bg-accent" : "hover:bg-accent/60"
            )}
          >
            <span
              className={cn(
                "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                choice === option.value && "border-foreground"
              )}
            >
              {choice === option.value && <span className="size-2 rounded-full bg-foreground" />}
            </span>
            <span>
              <span className="block font-medium">{option.title}</span>
              <span className="block text-muted-foreground">{option.description}</span>
            </span>
          </button>
        ))}
      </div>
      {choice !== "not_confirmed_yet" && (
        <div className="space-y-2">
          <Label htmlFor={`final-price-${booking.id}`}>Final booking price (total stay, ₹)</Label>
          <Input
            id={`final-price-${booking.id}`}
            type="number"
            inputMode="numeric"
            min={1}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>
      )}
      <Button onClick={save} disabled={saving} className="w-full sm:w-auto">
        {saving ? "Saving…" : "Save"}
      </Button>
    </div>
  );
}

function PriceSummary({ item, onEdit }: { item: BookingReconciliation; onEdit: () => void }) {
  const { booking } = item;
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border p-3 text-sm">
      <div>
        <p className="text-muted-foreground">Final booking price</p>
        <p className="text-lg font-semibold">{formatINR(booking.final_booking_price)}</p>
        <p className="text-xs text-muted-foreground">{PRICE_SOURCE_LABELS[booking.price_source] ?? ""}</p>
      </div>
      <Button size="sm" variant="ghost" onClick={onEdit}>
        Change
      </Button>
    </div>
  );
}

/**
 * Everything for one booking: summary, the matched conversation, "Mira
 * match?" and the final price. `item` is owned by the caller so a list can
 * update its own row from `onChanged`.
 */
export function BookingReconciliationBody({
  item,
  onChanged,
}: {
  item: BookingReconciliation;
  onChanged: (updated: BookingReconciliation) => void;
}) {
  const [editingPrice, setEditingPrice] = useState(false);
  const { booking } = item;
  const isReservation = booking.kind === "reservation";

  async function setKind(kind: "reservation" | "blocked") {
    try {
      onChanged(await api.bookings.updateKind(booking.id, kind));
      toast.success(kind === "blocked" ? "Marked as blocked dates" : "Marked as a guest booking");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't update this booking");
    }
  }

  const showPriceForm = isReservation && (editingPrice || booking.final_booking_price === null);

  return (
    <div className="space-y-5">
      <BookingSummary item={item} />

      {isReservation && item.conversation && (
        <section className="space-y-3">
          <ConversationCard item={item} />
          <MiraMatchControl item={item} onDecided={onChanged} />
        </section>
      )}

      {isReservation ? (
        <section className="space-y-3">
          <h3 className="text-sm font-medium">Final booking price</h3>
          {showPriceForm ? (
            <>
              {booking.price_status !== "confirmed" && booking.needs_price_confirmation && (
                <p className="text-sm text-muted-foreground">
                  {booking.detected_price !== null
                    ? "Confirm the price this booking was made at."
                    : "We couldn't confidently identify the final booking price."}
                </p>
              )}
              <FinalPriceForm
                // Re-seed the form whenever what it was seeded from changes
                // (e.g. a "No" on Mira match withdraws the detected price).
                key={`${booking.id}:${booking.detected_price ?? "none"}`}
                item={item}
                onSaved={(updated) => {
                  setEditingPrice(false);
                  onChanged(updated);
                }}
              />
            </>
          ) : (
            <PriceSummary item={item} onEdit={() => setEditingPrice(true)} />
          )}
          <button
            type="button"
            onClick={() => setKind("blocked")}
            className="text-xs text-muted-foreground underline-offset-4 hover:underline"
          >
            Not a guest booking? Mark as blocked dates
          </button>
        </section>
      ) : (
        <section className="space-y-2 text-sm text-muted-foreground">
          <p>These dates are blocked with no guest stay, so they aren&apos;t counted as a booking in Analytics.</p>
          <button
            type="button"
            onClick={() => setKind("reservation")}
            className="text-xs text-foreground underline-offset-4 hover:underline"
          >
            This was a guest booking
          </button>
        </section>
      )}
    </div>
  );
}

/** RightPanel wrapper that loads one booking's reconciliation view by id. */
export function BookingReconciliationPanel({
  bookingId,
  onOpenChange,
  onChanged,
  footer,
  children,
}: {
  bookingId: string | null;
  onOpenChange: (open: boolean) => void;
  onChanged?: () => void;
  footer?: React.ReactNode;
  /** Extra caller-specific content under the reconciliation sections. */
  children?: React.ReactNode;
}) {
  const { data, loading, error, setData } = useAsync(
    () => (bookingId ? api.bookings.reconciliation(bookingId) : Promise.resolve(null)),
    [bookingId]
  );

  return (
    <RightPanel open={!!bookingId} onOpenChange={onOpenChange} title="Booking details" size="lg" footer={footer}>
      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : error || !data ? (
        <p className="text-sm text-muted-foreground">Couldn&apos;t load this booking. Try again in a moment.</p>
      ) : (
        <div className="space-y-5">
          <BookingReconciliationBody
            key={data.booking.id}
            item={data}
            onChanged={(updated) => {
              setData(updated);
              onChanged?.();
            }}
          />
          {children}
        </div>
      )}
    </RightPanel>
  );
}
