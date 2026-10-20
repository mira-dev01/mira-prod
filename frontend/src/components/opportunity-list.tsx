import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { ListRow, ListRowFooter, ListRowHeader } from "@/components/ui/list-row";
import { StatusChip } from "@/components/status-chip";
<<<<<<< HEAD
import { formatLeadTimestamp, leadGuestLabel, leadPhoneLabel } from "@/lib/leads";
=======
import {
  formatLeadTimestamp,
  leadGuestLabel,
  leadPhoneLabel,
  useLeadLabels,
} from "@/lib/leads";
>>>>>>> 368377a (20th October)
import { opportunityType } from "@/lib/opportunities";
import type { LeadOut } from "@/lib/types";

// hot/warm/cold reused verbatim -- same tone vocabulary lead-detail-panel.tsx
// already established, not a new opportunity-specific palette.
<<<<<<< HEAD
const temperatureTone: Record<string, "destructive" | "pending" | "neutral"> = {
  hot: "destructive",
  warm: "pending",
  cold: "neutral",
};
=======
>>>>>>> 368377a (20th October)

/**
 * Shared row list for any opportunity type (see lib/opportunities.ts) --
 * generic over LeadOut.recovery_reason rather than hardcoding Busy Call
 * Recovery-specific copy, so a future opportunity type renders through the
 * exact same component. Deliberately not LeadsTable (dashboard/leads/
 * page.tsx) -- that component is booking-lead-specific (temperature-driven
 * Kanban drag, date columns); this mirrors LiveRequestsCard's simpler
 * ListRow-based shape instead, which fits an opportunity's own summary/
 * follow-up framing better.
 */
export function OpportunityList({
  leads,
  onCardClick,
}: {
  leads: LeadOut[];
  onCardClick: (lead: LeadOut) => void;
}) {
<<<<<<< HEAD
=======
  const leadLabels = useLeadLabels();
>>>>>>> 368377a (20th October)
  if (leads.length === 0) {
    return <p className="text-sm text-muted-foreground">No opportunities right now — you&rsquo;re all caught up.</p>;
  }

  return (
    <div className="space-y-3">
      {leads.map((lead) => {
        const type = opportunityType(lead);
        return (
          <ListRow key={lead.id} variant="boxed" interactive onClick={() => onCardClick(lead)}>
            <ListRowHeader>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                {type && <StatusChip status={type.label} tone="orange" />}
<<<<<<< HEAD
                {lead.lead_temperature && (
                  <StatusChip status={lead.lead_temperature} tone={temperatureTone[lead.lead_temperature] ?? "neutral"} />
                )}
=======
                <StatusChip status={leadLabels.label(lead.lead_temperature)} tone={leadLabels.tone(lead.lead_temperature)} className="normal-case" />
>>>>>>> 368377a (20th October)
                <span className="text-sm font-medium">{leadGuestLabel(lead)}</span>
                <span className="text-xs text-muted-foreground">{leadPhoneLabel(lead)}</span>
              </div>
              <span className="whitespace-nowrap text-xs text-muted-foreground">{formatLeadTimestamp(lead.updated_at)}</span>
            </ListRowHeader>
            <p className="text-sm leading-relaxed">
              {lead.conversation_summary ?? `${leadGuestLabel(lead)} needs follow-up.`}
            </p>
            <ListRowFooter className="flex-wrap justify-between pt-1">
              <div className="flex items-center gap-2">
                {lead.properties_discussed.length > 0 && (
                  <Badge variant="outline">{lead.properties_discussed[0]}</Badge>
                )}
                {lead.next_follow_up && (
                  <span className="text-xs text-muted-foreground">{lead.next_follow_up}</span>
                )}
              </div>
              {lead.call_session_id && (
                <Link href={`/dashboard/calls/${lead.call_session_id}`} onClick={(e) => e.stopPropagation()}>
                  <span className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground">
                    View call
                  </span>
                </Link>
              )}
            </ListRowFooter>
          </ListRow>
        );
      })}
    </div>
  );
}
