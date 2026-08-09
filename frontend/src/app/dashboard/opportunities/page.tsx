"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DateRangePicker } from "@/components/date-range-picker";
import { LeadDetailPanel } from "@/components/lead-detail-panel";
import { OpportunityList } from "@/components/opportunity-list";
import { RecoveryAnalyticsCard } from "@/components/recovery-analytics-card";
import { useAsync } from "@/hooks/use-async";
import { useNotificationStream } from "@/hooks/use-notification-stream";
import { api } from "@/lib/api";
import { matchesSearch, isBrowserTestIdentity } from "@/lib/utils";
import { OPPORTUNITY_TYPES, isOpportunity } from "@/lib/opportunities";
import type { LeadOut } from "@/lib/types";

function OpportunitiesPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const validReasons = OPPORTUNITY_TYPES.map((t) => t.reason);
  const initialTab = searchParams.get("type");
  const [tab, setTab] = useState<string>(
    initialTab && validReasons.includes(initialTab) ? initialTab : OPPORTUNITY_TYPES[0].reason
  );

  // Unfiltered by date range, same reasoning as Overview's Live Requests
  // card -- this is "what needs follow-up right now," not a report scoped
  // to a date picker.
  const { data: leads, loading, refetch } = useAsync(() => api.leads.list({}), []);
  const [editing, setEditing] = useState<LeadOut | null>(null);
  const [search, setSearch] = useState("");

  // Real-time updates: shared subscription (hooks/use-notification-stream.ts)
  // -- RecoveryService writes a channel="busy_recovery" Notification
  // whenever a new opportunity lead is created/touched (backend/app/
  // services/recovery_service.py). Filters for that channel specifically
  // and refetches leads, same "stream is a signal to refetch, not a data
  // source" pattern as LiveRequestsCard -- no new backend endpoint, no
  // second physical connection alongside whatever else on this page (e.g.
  // OpportunitiesCard, if this page is ever composed with it) is already
  // subscribed.
  useNotificationStream((notification) => {
    if (notification.channel === "busy_recovery") refetch();
  });

  function handleTabChange(value: unknown) {
    if (typeof value !== "string" || !validReasons.includes(value)) return;
    setTab(value);
    router.replace(`/dashboard/opportunities?type=${value}`, { scroll: false });
  }

  const opportunityLeads = (leads ?? []).filter(isOpportunity);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">Opportunities</h1>
          <p className="text-sm text-muted-foreground">
            Leads that entered outside a normal completed call and need host follow-up.
          </p>
        </div>
        <DateRangePicker />
      </div>

      {/* Only Busy Call Recovery has analytics today -- OPPORTUNITY_TYPES'
          own extensibility point (see lib/opportunities.ts) is where a
          future opportunity type's analytics would be added alongside this,
          not a reason to hide this one behind the tab selection. */}
      <RecoveryAnalyticsCard />

      <Tabs value={tab} onValueChange={handleTabChange}>
        <TabsList>
          {OPPORTUNITY_TYPES.map((type) => {
            const count = opportunityLeads.filter((lead) => lead.recovery_reason === type.reason).length;
            return (
              <TabsTrigger key={type.reason} value={type.reason}>
                {type.label}
                {count > 0 && <span className="ml-1.5 text-xs text-muted-foreground">({count})</span>}
              </TabsTrigger>
            );
          })}
        </TabsList>

        {OPPORTUNITY_TYPES.map((type) => {
          const typeLeads = opportunityLeads
            .filter((lead) => lead.recovery_reason === type.reason)
            .filter((lead) => matchesSearch(search, [
              lead.guest_name,
              isBrowserTestIdentity(lead.phone) ? null : lead.phone,
              lead.conversation_summary,
              lead.next_follow_up,
              ...lead.properties_discussed,
            ]))
            .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());

          return (
            <TabsContent key={type.reason} value={type.reason} className="space-y-4 pt-4">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-muted-foreground">{type.description}</p>
                {opportunityLeads.length > 0 && (
                  <Input
                    placeholder="Search…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    leadingIcon={<Search />}
                    className="w-64"
                  />
                )}
              </div>

              {loading ? (
                <Skeleton className="h-64 w-full" />
              ) : (
                <OpportunityList leads={typeLeads} onCardClick={setEditing} />
              )}
            </TabsContent>
          );
        })}
      </Tabs>

      <LeadDetailPanel lead={editing} onOpenChange={(open) => !open && setEditing(null)} onSaved={refetch} />
    </div>
  );
}

export default function OpportunitiesPage() {
  // useSearchParams requires a Suspense boundary -- same pattern as
  // dashboard/leads/page.tsx.
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <OpportunitiesPageContent />
    </Suspense>
  );
}
