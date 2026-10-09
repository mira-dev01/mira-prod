import { isBrowserTestIdentity } from "@/lib/utils";
import type { StatusTone } from "@/lib/tone";
import type { LeadOut, LeadTemperature } from "@/lib/types";

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

/**
 * Lead temperature, hottest first -- definitions in
 * backend/app/services/lead_temperature.py. very_hot (explicit booking
 * intent) shares hot's red tone: both mean "act now", the label tells
 * them apart.
 */
export const LEAD_TEMPERATURES: LeadTemperature[] = ["very_hot", "hot", "warm", "cold"];

const LEAD_TEMPERATURE_LABELS: Record<LeadTemperature, string> = {
  very_hot: "Booking intent",
  hot: "Hot",
  warm: "Warm",
  cold: "Cold",
};

export const leadTemperatureTone: Record<string, StatusTone> = {
  very_hot: "destructive",
  hot: "destructive",
  warm: "pending",
  cold: "neutral",
};

export function leadTemperatureLabel(value: string): string {
  return LEAD_TEMPERATURE_LABELS[value as LeadTemperature] ?? value;
}
