import type { StatusTone } from "@/lib/tone";
import type { TicketKind, TicketOut } from "@/lib/types";

/** How long a ticket may stay open before it is "SLA breached": one day, whatever its urgency. */
export const SLA_MS = 24 * 60 * 60 * 1000;

/** The number a host sees and quotes: TK-1033. */
export function ticketCode(ticket: Pick<TicketOut, "number">): string {
  return `TK-${ticket.number}`;
}

/** The dot colour of a kind's chip: red for what Mira promised, amber for a follow-up, blue for photos and messages. */
export const KIND_TONE: Record<TicketKind, StatusTone> = {
  promise: "destructive",
  escalation: "pending",
  photos: "progress",
  whatsapp: "progress",
  transfer: "pending",
  missed_call: "destructive",
  technician: "orange",
  payment: "pending",
  request: "neutral",
  stay_request: "purple",
};

const PROMISE_HEADINGS = ["Also promised on the call:", "Mira told the guest:"];

/**
 * What Mira told the guest would happen, one line each. They are the "- ..." lines under the marker the server writes
 * ("Also promised on the call:" on a ticket that was already open, "Mira told the guest:" on one made from the
 * promises alone). A promise ticket with no such lines is itself the promise.
 */
export function ticketPromises(ticket: Pick<TicketOut, "detail" | "kind" | "summary">): string[] {
  const detail = ticket.detail ?? "";
  for (const heading of PROMISE_HEADINGS) {
    const at = detail.indexOf(heading);
    if (at < 0) continue;
    const lines = detail
      .slice(at + heading.length)
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("-"))
      .map((line) => line.replace(/^-+\s*/, "").trim())
      .filter(Boolean);
    if (lines.length > 0) return lines;
  }
  return ticket.kind === "promise" ? [ticket.summary] : [];
}

/** The grey line under a ticket's title: its first promise, else the first line of what the server recorded. */
export function ticketSubtitle(ticket: TicketOut): string | null {
  const [promise] = ticketPromises(ticket);
  if (promise) return `Promised: ${promise}`;
  const first = (ticket.detail ?? "").split("\n").find((line) => line.trim());
  return first ? first.trim() : null;
}

/** The ticket's text without the promise lines (the panel lists those on their own). */
export function ticketDetailText(ticket: Pick<TicketOut, "detail" | "kind" | "summary">): string {
  const detail = ticket.detail ?? "";
  if (ticketPromises(ticket).length === 0) return detail.trim();
  const at = PROMISE_HEADINGS.map((heading) => detail.indexOf(heading)).filter((i) => i >= 0);
  return (at.length > 0 ? detail.slice(0, Math.min(...at)) : detail).trim();
}
