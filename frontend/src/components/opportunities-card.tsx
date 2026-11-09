"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { CardContent, CardHeader } from "@/components/ui/card";
import { CellCard, CellTitle } from "@/components/ui/sheet";
import { OpportunityList } from "@/components/opportunity-list";
import { useNotificationStream } from "@/hooks/use-notification-stream";
import { isOpportunity } from "@/lib/opportunities";
import { cn } from "@/lib/utils";
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
  className,
}: {
  leads: LeadOut[];
  onRefetch: () => void;
  onCardClick: (lead: LeadOut) => void;
  limit?: number;
  /** Grid placement inside the Overview Sheet (e.g. "md:col-span-6"). */
  className?: string;
}) {
  useNotificationStream((notification) => {
    if (notification.channel === "busy_recovery") onRefetch();
  });

  const opportunityLeads = leads
    .filter(isOpportunity)
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  const visibleLeads = limit ? opportunityLeads.slice(0, limit) : opportunityLeads;

  return (
    <CellCard className={cn("md:col-span-6", className)}>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CellTitle>Missed calls</CellTitle>
        <Badge variant="outline">{opportunityLeads.length} open</Badge>
      </CardHeader>
      <CardContent className="flex-1">
        <OpportunityList leads={visibleLeads} onCardClick={onCardClick} />
      </CardContent>
      {limit && opportunityLeads.length > limit && (
        <div className="border-t border-border px-5 py-3">
          <Link
            href="/dashboard/leads?tab=missed"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            View all {opportunityLeads.length}
            <ArrowRight className="size-3.5" />
          </Link>
        </div>
      )}
    </CellCard>
  );
}
