"use client";

import { useState } from "react";
import { toast } from "sonner";
import { BookingReconciliationPanel } from "@/components/booking-reconciliation";
import { RightPanelFooterButton } from "@/components/ui/right-panel";
import { Hint } from "@/components/ui/sheet";
import { api, ApiError } from "@/lib/api";

/** One booking's details (price, "Mira match?") with the unblock action, shared by every calendar view. */
export function BookingDetailsPanel({
  bookingId,
  onClose,
  onChanged,
}: {
  bookingId: string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [unblocking, setUnblocking] = useState(false);

  async function handleUnblock() {
    if (!bookingId) return;
    setUnblocking(true);
    try {
      await api.bookings.cancel(bookingId);
      toast.success("Dates unblocked");
      onClose();
      onChanged();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to unblock dates");
    } finally {
      setUnblocking(false);
    }
  }

  return (
    <BookingReconciliationPanel
      bookingId={bookingId}
      onOpenChange={(open) => !open && onClose()}
      onChanged={onChanged}
      footer={
        <RightPanelFooterButton variant="destructive" onClick={handleUnblock} disabled={unblocking}>
          {unblocking ? "Unblocking…" : "Unblock these dates"}
        </RightPanelFooterButton>
      }
    >
      <Hint>
        Unblock for a cancellation or dates blocked by mistake. If this came from Airbnb and is still active there, it
        reappears on the next iCal sync.
      </Hint>
    </BookingReconciliationPanel>
  );
}
