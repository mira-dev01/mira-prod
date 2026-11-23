import type { CallSessionOut } from "@/lib/types";

/** What a call looks like in a phone's call log. Anything finer (why it failed, who was
 * transferred, the summary) lives on the Calls tab. */
export type CallLogState = "incoming" | "outgoing" | "missed" | "failed";

// Mira only takes inbound calls today, so every connected call is Incoming. When outgoing
// calls land (OUTGOING_CALLS_PLAN.md) the call row will carry a direction: read it here and
// "Outgoing" starts showing, nowhere else needs to change.
function callDirection(): "incoming" | "outgoing" {
  return "incoming";
}

// The guest called and nobody could take it: Mira was already on a call, or the host did not
// pick up a transfer. Any other call the provider marked failed (busy, cancelled) is a miss too.
const MISSED_TYPES = new Set(["MISSED_AGENT_BUSY", "TRANSFERRED_TO_HOST_MISSED"]);

export function callLogState(call: CallSessionOut): CallLogState {
  // Mira's own side broke, or the call never reached her.
  if (call.call_type === "MISSED_SYSTEM_FAILURE") return "failed";
  if (MISSED_TYPES.has(call.call_type) || call.status === "failed") return "missed";
  return callDirection();
}

export const CALL_LOG_LABEL: Record<CallLogState, string> = {
  incoming: "Incoming",
  outgoing: "Outgoing",
  missed: "Missed",
  failed: "Failed",
};
