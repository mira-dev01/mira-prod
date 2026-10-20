import { useAuth } from "@/lib/auth-context";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { StatusTone } from "@/lib/tone";
import type { LeadBucket, LeadLabels, LeadOut, LeadTemperature } from "@/lib/types";

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
 * The dashboard's four lead tiers over the internal temperature levels
 * (definitions in backend/app/services/lead_temperature.py): very_hot shows
 * as Hot, and a lead with no temperature yet is "Not qualified". The names
 * are the host's own (Settings > Notifications); same mapping as the
 * backend's notification_preferences_service.lead_bucket.
 */
export const LEAD_BUCKETS: LeadBucket[] = ["hot", "warm", "cold", "not_qualified"];

export const DEFAULT_LEAD_LABELS: LeadLabels = {
  hot: "Hot",
  warm: "Warm",
  cold: "Cold",
  not_qualified: "Not qualified",
};

const LEAD_BUCKET_TONE: Record<LeadBucket, StatusTone> = {
  hot: "destructive",
  warm: "pending",
  cold: "neutral",
  not_qualified: "low",
};

export function leadBucket(temperature: string | null | undefined): LeadBucket {
  if (temperature === "hot" || temperature === "very_hot") return "hot";
  if (temperature === "warm" || temperature === "cold") return temperature;
  return "not_qualified";
}

/** The temperature to store when a host picks a tier: never downgrades a
 * very_hot lead picked as Hot; "Not qualified" clears it. */
export function temperatureForBucket(bucket: LeadBucket, current: string | null): LeadTemperature | null {
  if (bucket === "not_qualified") return null;
  if (bucket === "hot" && current === "very_hot") return "very_hot";
  return bucket;
}

/** Label and tone for a lead's temperature, in the host's own words. */
export function useLeadLabels() {
  const { user } = useAuth();
  const labels = user?.notification_preferences.lead_labels ?? DEFAULT_LEAD_LABELS;
  return {
    labels,
    label: (temperature: string | null | undefined) => labels[leadBucket(temperature)],
    tone: (temperature: string | null | undefined) => LEAD_BUCKET_TONE[leadBucket(temperature)],
  };
}
