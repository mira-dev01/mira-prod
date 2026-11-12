"use client";

// SPACEZ ONLY. The team's yes / no on a guest's early check-in / late check-out request, shown on its
// "stay_request" ticket (Leads -> Tickets). Yes: the guest is sent a payment link for the flat fee on
// WhatsApp. No: the guest is told on WhatsApp that the team can't do it. See
// backend/app/services/spacez_stay_requests.py.

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";

export function SpacezStayRequestActions({ ticketId, onAnswered }: { ticketId: string; onAnswered: () => void }) {
  const [busy, setBusy] = useState(false);

  async function answer(yes: boolean) {
    setBusy(true);
    try {
      await (yes ? api.spacezStayRequests.approve(ticketId) : api.spacezStayRequests.decline(ticketId));
      toast.success(yes ? "Payment link sent to the guest on WhatsApp" : "The guest has been told on WhatsApp");
      onAnswered();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not send the answer");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button size="sm" disabled={busy} onClick={() => answer(true)}>
        Yes, send payment link
      </Button>
      <Button size="sm" variant="outline" disabled={busy} onClick={() => answer(false)}>
        No, tell the guest
      </Button>
    </>
  );
}
