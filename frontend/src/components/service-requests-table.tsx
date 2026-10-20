"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ExpandableText } from "@/components/expandable-text";
import { RightPanel } from "@/components/ui/right-panel";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import type { ServiceRequestOut } from "@/lib/types";

// Same urgency vocabulary as calls-table.tsx's callUrgencyTone -- Notification
// and CallSession both use low|medium|high|emergency.
const urgencyTone: Record<string, StatusTone> = {
  emergency: "destructive",
  high: "destructive",
  medium: "pending",
  low: "neutral",
};

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleString();
}

/**
 * Shared row rendering for both the live (unresolved) and completed
 * (dismissed, reference-only) Service Request lists -- structured like
 * calls-table.tsx's CallsTable. `selectable` controls whether the leading
 * checkbox column renders; checking it dismisses that request immediately
 * (no bulk-select/confirm step). The completed/"recycle bin" view is
 * read-only, so it never renders the column.
 */
function ServiceRequestsList({
  requests,
  selectable,
  completing,
  onComplete,
}: {
  requests: ServiceRequestOut[];
  selectable: boolean;
  completing?: Set<string>;
  onComplete?: (callSessionId: string) => void;
}) {
  const router = useRouter();

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            {selectable && <TableHead className="w-12">Done</TableHead>}
            <TableHead>Logged at</TableHead>
            <TableHead>Property</TableHead>
            <TableHead>Room</TableHead>
            <TableHead>Request</TableHead>
            <TableHead>Urgency</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {requests.map((req) => (
            <TableRow key={req.call_session_id}>
              {selectable && (
                <TableCell
                  className="cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (!completing?.has(req.call_session_id)) onComplete?.(req.call_session_id);
                  }}
                >
                  <Checkbox
                    checked={completing?.has(req.call_session_id) ?? false}
                    disabled={completing?.has(req.call_session_id) ?? false}
                    onCheckedChange={() => onComplete?.(req.call_session_id)}
                    aria-label="Mark request as completed"
                  />
                </TableCell>
              )}
              <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                {formatTimestamp(req.created_at)}
              </TableCell>
              <TableCell className="max-w-[220px]">
                {req.property_name ? <ExpandableText text={req.property_name} maxLength={40} /> : "—"}
              </TableCell>
              <TableCell>{req.room_number ?? "—"}</TableCell>
              <TableCell className="max-w-md">
                <ExpandableText text={req.message} maxLength={90} />
              </TableCell>
              <TableCell>
                <StatusChip status={req.urgency} tone={urgencyTone[req.urgency] ?? "neutral"} />
              </TableCell>
              <TableCell>
                <Button variant="link" size="sm" onClick={() => router.push(`/dashboard/calls/${req.call_session_id}`)}>
                  View call
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function ServiceRequestsTable({
  requests,
  onDismiss,
}: {
  requests: ServiceRequestOut[];
  onDismiss: (callSessionIds: string[]) => Promise<void>;
}) {
  const [completing, setCompleting] = useState<Set<string>>(new Set());
  const [binOpen, setBinOpen] = useState(false);

  async function handleComplete(callSessionId: string) {
    setCompleting((current) => new Set(current).add(callSessionId));
    try {
      await onDismiss([callSessionId]);
    } finally {
      setCompleting((current) => {
        const next = new Set(current);
        next.delete(callSessionId);
        return next;
      });
    }
  }

  return (
    <div className="space-y-3">
      {requests.length === 0 ? (
        <p className="text-sm text-muted-foreground">No open service requests.</p>
      ) : (
        <ServiceRequestsList requests={requests} selectable completing={completing} onComplete={handleComplete} />
      )}

      <div>
        <Button variant="link" size="sm" className="px-0 text-muted-foreground" onClick={() => setBinOpen(true)}>
          View completed requests
        </Button>
      </div>

      <RightPanel open={binOpen} onOpenChange={setBinOpen} title="Completed service requests" size="xl">
        <CompletedRequestsPanel open={binOpen} />
      </RightPanel>
    </div>
  );
}

function CompletedRequestsPanel({ open }: { open: boolean }) {
  const { data: dismissed, loading } = useAsync(
    () => (open ? api.leads.serviceRequests({ includeDismissed: true }) : Promise.resolve([])),
    [open]
  );
  const completed = (dismissed ?? []).filter((r) => r.dismissed_at !== null);

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (completed.length === 0) {
    return <p className="text-sm text-muted-foreground">No completed requests yet.</p>;
  }
  return <ServiceRequestsList requests={completed} selectable={false} />;
}
