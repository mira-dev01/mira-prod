"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ListRow, ListRowFooter, ListRowHeader } from "@/components/ui/list-row";
import { StatusChip } from "@/components/status-chip";
import { leadUrgencyTone } from "@/components/lead-detail-panel";
import { API_BASE_URL, ApiError, api, getToken } from "@/lib/api";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { LeadOut, NotificationOut } from "@/lib/types";

function leadGuestLabel(lead: LeadOut): string {
  return isBrowserTestIdentity(lead.phone) ? "Browser test" : lead.guest_name ?? "Unknown guest";
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Overview's "Live requests" card -- previously its own NotificationsFeed
 * component reading directly from the Notification/SSE stream. Now a thin
 * filtered view of Leads (escalated === true), per restructure.md Phase 3:
 * Live Requests is no longer a separate concept from Leads, just a lens on
 * it. The backend Notification/SSE mechanism is unchanged and still the
 * real-time transport -- this component listens to the same stream purely
 * as a "something escalated, go refetch leads" signal, not to render
 * Notification objects directly.
 */
export function LiveRequestsCard({
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
  const seenNotificationIds = useRef(new Set<string>());
  const [handlingIds, setHandlingIds] = useState<Set<string>>(new Set());

  async function markHandled(lead: LeadOut) {
    setHandlingIds((prev) => new Set(prev).add(lead.id));
    try {
      await api.leads.update(lead.id, { status: "closed" });
      onRefetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update request");
      setHandlingIds((prev) => {
        const next = new Set(prev);
        next.delete(lead.id);
        return next;
      });
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    const token = getToken();

    async function streamNotifications() {
      try {
        const res = await fetch(`${API_BASE_URL}/notifications/stream`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          signal: controller.signal,
        });
        if (!res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const events = buffer.split("\n\n");
          buffer = events.pop() ?? "";

          for (const event of events) {
            const line = event.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            const notification = JSON.parse(line.slice("data: ".length)) as NotificationOut;
            if (seenNotificationIds.current.has(notification.id)) continue;
            seenNotificationIds.current.add(notification.id);
            // A new escalation notification means the Lead it's tied to
            // just changed (created or updated) -- refetch leads so this
            // card and the count stay live, same as the old feed did.
            onRefetch();
          }
        }
      } catch {
        // connection closed (navigation, unmount, or backend restart) — nothing to do
      }
    }

    streamNotifications();
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onRefetch is a stable useAsync refetch callback, re-subscribing on every render would tear down and rebuild the stream needlessly
  }, []);

  const escalatedLeads = leads
    .filter((lead) => lead.status === "open" && (lead.escalated || lead.urgency))
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  const visibleLeads = limit ? escalatedLeads.slice(0, limit) : escalatedLeads;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Live requests</CardTitle>
        <Badge variant="outline">{escalatedLeads.length} active</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {escalatedLeads.length === 0 && (
          <p className="text-sm text-muted-foreground">Nothing pending — you're all caught up.</p>
        )}
        {visibleLeads.map((lead) => (
          <ListRow key={lead.id} variant="boxed" interactive onClick={() => onCardClick(lead)}>
            <ListRowHeader>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span
                  className="flex items-center"
                  onClick={(e) => e.stopPropagation()}
                  title="Mark as handled"
                >
                  <Checkbox
                    checked={handlingIds.has(lead.id)}
                    disabled={handlingIds.has(lead.id)}
                    onCheckedChange={(checked) => {
                      if (checked) markHandled(lead);
                    }}
                    aria-label="Mark request as handled"
                  />
                </span>
                {lead.urgency && <StatusChip status={lead.urgency} tone={leadUrgencyTone[lead.urgency] ?? "neutral"} />}
                <span className="text-sm font-medium">
                  {lead.properties_discussed[0] ?? leadGuestLabel(lead)}
                </span>
              </div>
              <span className="whitespace-nowrap text-xs text-muted-foreground">{formatTime(lead.updated_at)}</span>
            </ListRowHeader>
            <p className="text-sm leading-relaxed">
              {lead.conversation_summary ?? `${leadGuestLabel(lead)} needs follow-up.`}
            </p>
            {lead.call_session_id && (
              <ListRowFooter className="pt-1">
                <Link href={`/dashboard/calls/${lead.call_session_id}`} onClick={(e) => e.stopPropagation()}>
                  <span className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground">
                    View call
                  </span>
                </Link>
              </ListRowFooter>
            )}
          </ListRow>
        ))}
      </CardContent>
      {limit && escalatedLeads.length > limit && (
        <div className="border-t px-4 py-3">
          <Link
            href="/dashboard/leads?status=open"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            View all {escalatedLeads.length} live requests
            <ArrowRight className="size-3.5" />
          </Link>
        </div>
      )}
    </Card>
  );
}
