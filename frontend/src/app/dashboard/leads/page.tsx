"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ActionableCard, type ActionableCardPriority } from "@/components/actionable-card";
import { DateRangePicker } from "@/components/date-range-picker";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api, ApiError } from "@/lib/api";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { LeadOut } from "@/lib/types";

const TEMPERATURES = ["hot", "warm", "cold"] as const;

const temperaturePriority: Record<string, ActionableCardPriority> = {
  hot: { label: "Hot", tone: "high" },
  warm: { label: "Warm", tone: "medium" },
  cold: { label: "Cold", tone: "low" },
};

function leadTitle(lead: LeadOut): string {
  const name = isBrowserTestIdentity(lead.phone) ? "Browser test" : lead.guest_name ?? "Unknown guest";
  const destination = lead.properties_discussed.length > 0 ? lead.properties_discussed.join(", ") : null;
  return destination ? `${name} — ${destination}` : name;
}

function leadSummary(lead: LeadOut): string | undefined {
  const parts: string[] = [];
  if (lead.purpose_of_stay) parts.push(lead.purpose_of_stay);
  if (lead.conversation_summary) parts.push(lead.conversation_summary);
  return parts.length > 0 ? parts.join(" — ") : undefined;
}

function leadMetadata(lead: LeadOut): string {
  const parts: string[] = [];
  parts.push(isBrowserTestIdentity(lead.phone) ? "Browser test" : lead.phone ?? "No phone");
  if (lead.check_in && lead.check_out) parts.push(`${lead.check_in} → ${lead.check_out}`);
  if (lead.num_guests) parts.push(`${lead.num_guests} guest${lead.num_guests === 1 ? "" : "s"}`);
  if (lead.escalated) parts.push("Escalated");
  return parts.join(" · ");
}

export default function LeadsPage() {
  const { startDateISO, endDateISO } = useDateRange();
  const { data: leads, loading, refetch } = useAsync(
    () => api.leads.list({ startDate: startDateISO, endDate: endDateISO }),
    [startDateISO, endDateISO]
  );
  const [editing, setEditing] = useState<LeadOut | null>(null);
  const [temperature, setTemperature] = useState<string>("warm");
  const [nextFollowUp, setNextFollowUp] = useState("");
  const [summary, setSummary] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function openEdit(lead: LeadOut) {
    setEditing(lead);
    setTemperature(lead.lead_temperature ?? "warm");
    setNextFollowUp(lead.next_follow_up ?? "");
    setSummary(lead.conversation_summary ?? "");
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setSubmitting(true);
    try {
      await api.leads.update(editing.id, {
        lead_temperature: temperature as "hot" | "warm" | "cold",
        next_follow_up: nextFollowUp,
        conversation_summary: summary,
      });
      toast.success("Lead updated");
      setEditing(null);
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update lead");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">Leads</h1>
          <p className="text-sm text-muted-foreground">Booking enquiries qualified by the Lead Agent</p>
        </div>
        <DateRangePicker />
      </div>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : !leads || leads.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No leads yet — they appear here once your portfolio&apos;s lead intake number starts receiving calls.
        </p>
      ) : (
        <div className="space-y-3">
          {leads.map((lead) => (
            <ActionableCard
              key={lead.id}
              title={leadTitle(lead)}
              summary={leadSummary(lead)}
              metadata={leadMetadata(lead)}
              priority={lead.lead_temperature ? temperaturePriority[lead.lead_temperature] : undefined}
              onClick={() => openEdit(lead)}
            />
          ))}
        </div>
      )}

      <Dialog open={!!editing} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Edit lead —{" "}
              {editing && isBrowserTestIdentity(editing.phone)
                ? "Browser test"
                : editing?.guest_name ?? editing?.phone}
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSave} className="space-y-4">
            <div className="space-y-2">
              <Label>Temperature</Label>
              <Select value={temperature} onValueChange={(v) => v && setTemperature(v)}>
                <SelectTrigger>
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
              <Textarea id="summary" value={summary} onChange={(e) => setSummary(e.target.value)} />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={submitting}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
