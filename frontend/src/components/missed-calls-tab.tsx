"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { DateRangePicker } from "@/components/date-range-picker";
import { LeadDetailPanel } from "@/components/lead-detail-panel";
import { OpportunityList } from "@/components/opportunity-list";
import { RecoveryAnalyticsCard } from "@/components/recovery-analytics-card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Cell, Sheet, Toolbar } from "@/components/ui/sheet";
import { useAsync } from "@/hooks/use-async";
import { useNotificationStream } from "@/hooks/use-notification-stream";
import { api } from "@/lib/api";
import { isOpportunity } from "@/lib/opportunities";
import type { LeadOut } from "@/lib/types";
import { isBrowserTestIdentity, matchesSearch } from "@/lib/utils";

/**
 * Leads → "Missed calls": guests who reached Mira outside a normal completed
 * call (today: the line was busy and they were sent a WhatsApp follow-up).
 * These are ordinary leads with a recovery_reason, so they live inside the
 * Leads page rather than on a page of their own. New kinds of missed call
 * are a new entry in lib/opportunities.ts, not a new tab.
 */
export function MissedCallsTab() {
  // Unfiltered by date range: this is "what needs follow-up right now".
  const { data: leads, loading, refetch } = useAsync(() => api.leads.list({}), []);
  const [editing, setEditing] = useState<LeadOut | null>(null);
  const [search, setSearch] = useState("");

  // RecoveryService writes a channel="busy_recovery" notification whenever it
  // creates or touches one of these leads; the stream is a signal to refetch.
  useNotificationStream((notification) => {
    if (notification.channel === "busy_recovery") refetch();
  });

  const missed = (leads ?? []).filter(isOpportunity);
  const visible = missed
    .filter((lead) =>
      matchesSearch(search, [
        lead.guest_name,
        isBrowserTestIdentity(lead.phone) ? null : lead.phone,
        lead.conversation_summary,
        lead.next_follow_up,
        ...lead.properties_discussed,
      ])
    )
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());

  return (
    <>
      <Sheet>
        <Toolbar className="justify-between">
          {missed.length > 0 ? (
            <div className="w-full sm:w-64">
              <Input placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} leadingIcon={<Search />} />
            </div>
          ) : (
            <span />
          )}
          <DateRangePicker />
        </Toolbar>

        <RecoveryAnalyticsCard />

        <Cell>
          {loading ? <Skeleton className="h-64 w-full" /> : <OpportunityList leads={visible} onCardClick={setEditing} />}
        </Cell>
      </Sheet>

      <LeadDetailPanel lead={editing} onOpenChange={(open) => !open && setEditing(null)} onSaved={refetch} />
    </>
  );
}
