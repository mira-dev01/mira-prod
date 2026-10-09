import { StatusChip, type StatusTone } from "@/components/status-chip";
import type { CapabilityState } from "@/lib/types";

const STATE_LABEL: Record<CapabilityState, string> = {
  ready: "Ready",
  needs_setup: "Needs setup",
  disabled: "Off",
  available: "Available",
  unavailable: "Unavailable",
};

const STATE_TONE: Record<CapabilityState, StatusTone> = {
  ready: "live",
  needs_setup: "pending",
  disabled: "neutral",
  available: "neutral",
  unavailable: "low",
};

/** One capability's readiness, shared by Settings > Features & modules and
 * the onboarding review step so both read the same way. */
export function CapabilityStateChip({ state }: { state: CapabilityState }) {
  return <StatusChip status={STATE_LABEL[state]} tone={STATE_TONE[state]} />;
}
