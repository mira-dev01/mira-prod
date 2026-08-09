"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import type { LeadOut } from "@/lib/types";

/**
 * Final confirmation before a lead moves to Closed. Closing a lead
 * auto-creates a manual calendar block (see CLAUDE.md's Closed->Calendar
 * flow), so this is the one point where the host reviews the exact dates/
 * property/guest details that are about to be written to the shared
 * Calendar -- cheap to confirm, expensive to silently get wrong since it
 * blocks real availability across the portfolio.
 */
export function CloseLeadDialog({
  lead,
  onOpenChange,
  onConfirmed,
}: {
  lead: LeadOut | null;
  onOpenChange: (open: boolean) => void;
  onConfirmed: () => void;
}) {
  const { data: properties } = useAsync(() => api.properties.list(), []);
  const [propertyId, setPropertyId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  // Re-seed once per lead AND once properties finishes loading -- properties
  // is always still null on the dialog's first render (useAsync resolves it
  // async), so keying only on lead.id would lock propertyId to "" before
  // discussedMatches ever had a chance to compute against real data.
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  const discussedMatches =
    lead && properties ? properties.filter((p) => lead.properties_discussed.includes(p.name)) : [];

  if (lead && properties && loadedFor !== lead.id) {
    setPropertyId(discussedMatches.length === 1 ? discussedMatches[0].id : "");
    setLoadedFor(lead.id);
  }

  const hasDates = Boolean(lead?.check_in && lead?.check_out);

  async function handleConfirm() {
    if (!lead) return;
    setSubmitting(true);
    try {
      // Booking block only makes sense with real dates + a chosen property --
      // a lead can still be closed without either (e.g. a dead-end enquiry),
      // it just won't produce a calendar entry.
      if (hasDates && propertyId) {
        await api.bookings.create({
          property_id: propertyId,
          check_in: lead.check_in as string,
          check_out: lead.check_out as string,
          guest_name: lead.guest_name,
          guest_phone: lead.phone,
          platform: "manual",
        });
      }
      await api.leads.update(lead.id, { status: "closed" });
      toast.success(hasDates && propertyId ? "Lead closed and dates blocked on the calendar" : "Lead closed");
      onOpenChange(false);
      onConfirmed();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to close lead");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={!!lead} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Confirm booking</DialogTitle>
          <DialogDescription>
            Review the details below before closing this lead. Confirming will block these dates on your
            Calendar so you can update Airbnb accordingly.
          </DialogDescription>
        </DialogHeader>

        {lead && (
          <div className="space-y-3 text-sm">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs text-muted-foreground">Guest</p>
                <p className="font-medium">{lead.guest_name ?? "Unknown guest"}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Guests</p>
                <p className="font-medium">{lead.num_guests ?? "—"}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Check-in</p>
                <p className="font-medium">{lead.check_in ?? "—"}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Check-out</p>
                <p className="font-medium">{lead.check_out ?? "—"}</p>
              </div>
            </div>

            {(lead.occasion || lead.purpose_of_stay) && (
              <div>
                <p className="text-xs text-muted-foreground">Requirements / occasion</p>
                <p className="font-medium">{[lead.occasion, lead.purpose_of_stay].filter(Boolean).join(" · ")}</p>
              </div>
            )}

            {hasDates ? (
              <div className="space-y-2">
                <Label>Property to block on the calendar</Label>
                <Select value={propertyId} onValueChange={(v) => v && setPropertyId(v)}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Select a property">
                      {(value: string) => properties?.find((p) => p.id === value)?.name ?? "Select a property"}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {properties?.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {!propertyId && (
                  <p className="text-xs text-muted-foreground">
                    No property selected — this lead will close without blocking the calendar.
                  </p>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No check-in/check-out on this lead — closing it won&apos;t create a calendar block.
              </p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={submitting}>
            {submitting ? "Confirming…" : "Confirm and close"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
