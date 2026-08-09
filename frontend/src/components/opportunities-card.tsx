"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { OpportunityList } from "@/components/opportunity-list";
import { useNotificationStream } from "@/hooks/use-notification-stream";
import { isOpportunity } from "@/lib/opportunities";
import type { LeadOut } from "@/lib/types";

/**
 * Overview's compact "Opportunities" card -- same relationship to the full
 * /dashboard/opportunities page that LiveRequestsCard has to the Leads
 * page: a thin, limited-count filtered view of the same Lead data, real-time
 * via the same shared /notifications/stream subscription every dashboard
 * consumer rides (hooks/use-notification-stream.ts), filtered here to
 * channel="busy_recovery". No opportunity-specific backend endpoint; see
 * lib/opportunities.ts.
 */
export function OpportunitiesCard({
  leads,
  onRefetch,
  onCardClick,
  limit,
}: {
  leads: LeadOut[];
  onRefetch: () => void;
  onCardClick: (lead: LeadOut) => void;
  limit?: number;
}) {
  useNotificationStream((notification) => {
    if (notification.channel === "busy_recovery") onRefetch();
  });

  const opportunityLeads = leads
    .filter(isOpportunity)
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  const visibleLeads = limit ? opportunityLeads.slice(0, limit) : opportunityLeads;

  return (
    <Card className="h-full">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Opportunities</CardTitle>
        <Badge variant="outline">{opportunityLeads.length} open</Badge>
      </CardHeader>
      <CardContent className="flex-1 space-y-3">
        <OpportunityList leads={visibleLeads} onCardClick={onCardClick} />
      </CardContent>
      {limit && opportunityLeads.length > limit && (
        <div className="border-t px-4 py-3">
          <Link
            href="/dashboard/opportunities"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            View all {opportunityLeads.length} opportunities
            <ArrowRight className="size-3.5" />
          </Link>
        </div>
      )}
    </Card>
  );
}
