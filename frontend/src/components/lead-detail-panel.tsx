"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { CallSummaryCard } from "@/components/call-summary-card";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { cn, isBrowserTestIdentity } from "@/lib/utils";
import type { LeadOut, LeadStatus } from "@/lib/types";

const TEMPERATURES = ["hot", "warm", "cold"] as const;
export const LEAD_STATUSES: LeadStatus[] = ["open", "contacted", "booked", "closed"];

// Same vocabulary as calls-table.tsx's urgency tones -- Lead.urgency is
// backfilled server-side from the linked Notification (see
// backend/app/api/v1/leads.py), the same "does the host need to act right
// now" signal Live Requests used to show on its own before it was folded
// into the leads list/detail itself.
export const leadUrgencyTone: Record<string, StatusTone> = {
  emergency: "destructive",
  high: "destructive",
  medium: "pending",
  low: "neutral",
};

// Mini status stepper -- lets a host move a lead through
// Open -> Contacted -> Booked -> Closed without leaving the panel or
// dragging a Kanban card, per the "status (kanban) on it" request.
function LeadStatusStepper({ status, onSelect }: { status: LeadStatus; onSelect: (status: LeadStatus) => void }) {
  const currentIndex = LEAD_STATUSES.indexOf(status);
  return (
    <div className="flex items-center gap-1">
      {LEAD_STATUSES.map((s, i) => (
        <button
          key={s}
          type="button"
          onClick={() => onSelect(s)}
          className={cn(
            "flex-1 rounded-md border px-2 py-1.5 text-center text-xs font-medium capitalize transition-colors",
            i <= currentIndex
              ? "border-transparent bg-primary text-primary-foreground"
              : "border-input text-muted-foreground hover:bg-accent"
          )}
        >
          {s}
        </button>
      ))}
    </div>
  );
}

/**
 * Shared right-panel lead detail/edit view -- used by the Leads page
 * (table/Kanban row click) and Overview (live-request/escalated-lead card
 * click), so a lead opened from either surface behaves identically: same
 * status stepper, same "View call summary" redirect, same edit form.
 */
export function LeadDetailPanel({
  lead,
  onOpenChange,
  onSaved,
}: {
  lead: LeadOut | null;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const router = useRouter();
  const [temperature, setTemperature] = useState<string>("warm");
  const [status, setStatus] = useState<LeadStatus>("open");
  const [nextFollowUp, setNextFollowUp] = useState("");
  const [summary, setSummary] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  // Same call fetch as the calls detail page, so the AI summary rendered
  // here (via CallSummaryCard) is identical rather than just a link out.
  const { data: call } = useAsync(
    () => (lead?.call_session_id ? api.calls.get(lead.call_session_id) : Promise.resolve(null)),
    [lead?.call_session_id]
  );

  // Seed the form once per newly-opened lead, same pattern as the guest
  // profile page's edit form -- avoids stomping on in-progress edits if
  // `lead` identity changes for an unrelated reason while open.
  if (lead && loadedFor !== lead.id) {
    setTemperature(lead.lead_temperature ?? "warm");
    setStatus((lead.status as LeadStatus) ?? "open");
    setNextFollowUp(lead.next_follow_up ?? "");
    setSummary(lead.conversation_summary ?? "");
    setLoadedFor(lead.id);
  }

  async function handleStatusChange(newStatus: LeadStatus) {
    if (!lead || lead.status === newStatus) return;
    try {
      await api.leads.update(lead.id, { status: newStatus });
      toast.success(`Moved to ${newStatus}`);
      setStatus(newStatus);
      onSaved();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update lead status");
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!lead) return;
    setSubmitting(true);
    try {
      await api.leads.update(lead.id, {
        lead_temperature: temperature as "hot" | "warm" | "cold",
        status,
        next_follow_up: nextFollowUp,
        conversation_summary: summary,
      });
      toast.success("Lead updated");
      onOpenChange(false);
      onSaved();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update lead");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <RightPanel
      open={!!lead}
      onOpenChange={onOpenChange}
      size="lg"
      title={
        <>Edit lead — {lead && isBrowserTestIdentity(lead.phone) ? "Browser test" : lead?.guest_name ?? lead?.phone}</>
      }
      footer={
        <RightPanelFooterButton type="submit" form="edit-lead-form" disabled={submitting}>
          {submitting ? "Saving…" : "Save"}
        </RightPanelFooterButton>
      }
    >
      {lead && (
        <p className="text-xs text-muted-foreground">
          Received {new Date(lead.created_at).toLocaleString([], {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })}
        </p>
      )}

      {lead?.urgency && (
        <div className="flex items-center gap-2">
          <StatusChip status={lead.urgency} tone={leadUrgencyTone[lead.urgency] ?? "neutral"} />
          <span className="text-xs text-muted-foreground">Escalated during this call</span>
        </div>
      )}

      {lead?.call_session_id && (
        <>
          <CallSummaryCard summary={call?.ai_summary ?? null} />
          <Button variant="outline" size="sm" onClick={() => router.push(`/dashboard/calls/${lead.call_session_id}`)}>
            View full call
          </Button>
        </>
      )}

      <div className="space-y-2">
        <Label>Status</Label>
        <LeadStatusStepper status={status} onSelect={handleStatusChange} />
      </div>

      <form id="edit-lead-form" onSubmit={handleSave} className="space-y-4">
        <div className="space-y-2">
          <Label>Temperature</Label>
          <Select value={temperature} onValueChange={(v) => v && setTemperature(v)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TEMPERATURES.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="next-follow-up">Next follow-up</Label>
          <Input id="next-follow-up" value={nextFollowUp} onChange={(e) => setNextFollowUp(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="summary">Conversation summary</Label>
          <DictationTextarea id="summary" value={summary} onValueChange={setSummary} />
        </div>
      </form>
    </RightPanel>
  );
}
