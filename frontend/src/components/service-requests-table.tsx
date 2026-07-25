"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ExpandableText } from "@/components/expandable-text";
import { RightPanel } from "@/components/ui/right-panel";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
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
 * checkbox column renders; the completed/"recycle bin" view is read-only.
 */
function ServiceRequestsList({
  requests,
  selectable,
  selected,
  onToggle,
}: {
  requests: ServiceRequestOut[];
  selectable: boolean;
  selected?: Set<string>;
  onToggle?: (callSessionId: string, checked: boolean) => void;
}) {
  const router = useRouter();

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            {selectable && <TableHead className="w-8" />}
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
                <TableCell onClick={(e) => e.stopPropagation()}>
                  <Checkbox
                    checked={selected?.has(req.call_session_id) ?? false}
                    onCheckedChange={(checked) => onToggle?.(req.call_session_id, checked === true)}
                    aria-label="Select request"
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
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dismissing, setDismissing] = useState(false);
  const [binOpen, setBinOpen] = useState(false);

  function toggle(callSessionId: string, checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(callSessionId);
      else next.delete(callSessionId);
      return next;
    });
  }

  async function handleDismissSelected() {
    if (selected.size === 0) return;
    setDismissing(true);
    try {
      await onDismiss(Array.from(selected));
      setSelected(new Set());
    } finally {
      setDismissing(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end gap-2">
        {selected.size > 0 && (
          <span className="text-xs text-muted-foreground">{selected.size} selected</span>
        )}
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="Mark selected requests as completed"
          disabled={selected.size === 0 || dismissing}
          onClick={handleDismissSelected}
          className={cn(selected.size > 0 && "border-destructive text-destructive hover:bg-destructive/10")}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>

      {requests.length === 0 ? (
        <p className="text-sm text-muted-foreground">No open service requests.</p>
      ) : (
        <ServiceRequestsList requests={requests} selectable selected={selected} onToggle={toggle} />
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
