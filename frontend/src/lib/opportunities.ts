/**
 * Opportunities: a lens on Leads, exactly like Live Requests already is
 * (see live-requests-card.tsx's own docstring) -- not a new backend
 * resource. An "opportunity" is any Lead whose recovery_reason is set
 * (backend/app/models/lead.py's own comment: recovery_reason explains HOW/
 * WHY a lead entered outside a normal completed conversation, never a
 * Lead.status value). Phase 5 ships exactly one opportunity TYPE, Busy Call
 * Recovery (recovery_reason="BUSY_CALL"), but the whole page/tab/card layer
 * is written against this registry so a future type (e.g. "AFTER_HOURS",
 * already a reserved RecoveryReason value with no producer yet -- see
 * backend/app/schemas/lead.py) is a new entry here, not a new page, new
 * endpoint, or new set of components.
 */

import type { LucideIcon } from "lucide-react";
import { PhoneMissed } from "lucide-react";
import type { LeadOut } from "@/lib/types";

export type OpportunityType = {
  /** Matches LeadOut.recovery_reason exactly (backend RecoveryReason literal). */
  reason: string;
  label: string;
  description: string;
  icon: LucideIcon;
};

export const OPPORTUNITY_TYPES: OpportunityType[] = [
  {
    reason: "BUSY_CALL",
    label: "Busy Call Recovery",
    description: "Guests who called while every line was busy and were sent a WhatsApp follow-up automatically.",
    icon: PhoneMissed,
  },
  // Future types (AFTER_HOURS, HOST_CALLBACK, GUEST_CALLBACK -- reserved in
  // backend RecoveryReason, no producer yet) slot in here as additional
  // entries once something in the backend actually sets that reason.
];

export function isOpportunity(lead: LeadOut): boolean {
  return lead.recovery_reason != null;
}

export function opportunityType(lead: LeadOut): OpportunityType | undefined {
  return OPPORTUNITY_TYPES.find((type) => type.reason === lead.recovery_reason);
}
