"use client";

import { TicketsView } from "@/components/tickets-view";

// Tickets has its own place in the sidebar (it used to be a tab on Leads). The red number on the sidebar link is the
// open-ticket count, kept by booking-activity-context.tsx.
export default function TicketsPage() {
  return <TicketsView />;
}
