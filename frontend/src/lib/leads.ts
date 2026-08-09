import { isBrowserTestIdentity } from "@/lib/utils";
import type { LeadOut } from "@/lib/types";

/**
 * Shared lead display helpers -- previously copy-pasted identically across
 * opportunity-list.tsx, live-requests-card.tsx, and dashboard/leads/page.tsx.
 */

export function leadGuestLabel(lead: LeadOut): string {
  return isBrowserTestIdentity(lead.phone) ? "Browser test" : lead.guest_name ?? "Unknown guest";
}

export function leadPhoneLabel(lead: LeadOut): string {
  return isBrowserTestIdentity(lead.phone) ? "Browser test" : lead.phone ?? "No phone";
}

export function formatLeadTimestamp(iso: string): string {
  return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
