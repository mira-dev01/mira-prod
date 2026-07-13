"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ChevronRight, LayoutGrid, Table2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { DateRangePicker } from "@/components/date-range-picker";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api, ApiError } from "@/lib/api";
import { cn, isBrowserTestIdentity } from "@/lib/utils";
import type { LeadOut, LeadStatus } from "@/lib/types";

const TEMPERATURES = ["hot", "warm", "cold"] as const;
const STATUSES: LeadStatus[] = ["open", "contacted", "booked", "closed"];

// hot/warm/cold is a literal temperature metaphor -- red (urgent/act-now),
// amber (warming up), neutral gray (not yet) -- reusing the same tones the
// Calls/Overview pages use for status, so "what does this color mean"
// stays answerable from one system instead of a lead-specific palette.
const temperatureTone: Record<string, StatusTone> = {
  hot: "destructive",
  warm: "pending",
  cold: "neutral",
};

// status is the host's own follow-up lifecycle, separate from temperature
// (see CLAUDE.md) -- open needs attention (pending/amber), contacted is in
// motion (progress/blue), booked is the successful end state (live/green),
// closed is done either way (neutral/gray).
const statusTone: Record<string, StatusTone> = {
  open: "pending",
  contacted: "progress",
  booked: "live",
  closed: "neutral",
};

const temperatureRank: Record<string, number> = { hot: 0, warm: 1, cold: 2 };

// A lead the voice agent never got anywhere with -- no name, no phone, no
// summary. These are real rows (every escalated/qualifying call creates
// one) but carry nothing a host can act on, so they shouldn't compete with
// hot/warm leads for attention at the top of the list.
function isEmptyLead(lead: LeadOut): boolean {
  const hasIdentity = Boolean(lead.guest_name) || (Boolean(lead.phone) && !isBrowserTestIdentity(lead.phone));
  const hasContent = Boolean(
    lead.purpose_of_stay || lead.conversation_summary || lead.occasion || lead.properties_discussed.length > 0
  );
  return !hasIdentity && !hasContent;
}

function leadGuestLabel(lead: LeadOut): string {
  return isBrowserTestIdentity(lead.phone) ? "Browser test" : lead.guest_name ?? "Unknown guest";
}

function leadPhoneLabel(lead: LeadOut): string {
  return isBrowserTestIdentity(lead.phone) ? "Browser test" : lead.phone ?? "No phone";
}

function leadDatesLabel(lead: LeadOut): string {
  return lead.check_in && lead.check_out ? `${lead.check_in} → ${lead.check_out}` : "—";
}

function LeadsTable({
  leads,
  onRowClick,
  muted,
}: {
  leads: LeadOut[];
  onRowClick: (lead: LeadOut) => void;
  muted?: boolean;
}) {
  return (
    <div className={cn("overflow-x-auto", muted && "opacity-60")}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Guest</TableHead>
            <TableHead>Property</TableHead>
            <TableHead>Dates</TableHead>
            <TableHead>Guests</TableHead>
            <TableHead>Temperature</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Phone</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {leads.map((lead) => (
            <TableRow key={lead.id} className="cursor-pointer" onClick={() => onRowClick(lead)}>
              <TableCell className="font-medium">{leadGuestLabel(lead)}</TableCell>
              <TableCell>
                {lead.properties_discussed.length > 0 ? lead.properties_discussed.join(", ") : "—"}
              </TableCell>
              <TableCell>{leadDatesLabel(lead)}</TableCell>
              <TableCell>{lead.num_guests ?? "—"}</TableCell>
              <TableCell>
                {lead.lead_temperature ? (
                  <StatusChip status={lead.lead_temperature} tone={temperatureTone[lead.lead_temperature]} />
                ) : (
                  "—"
                )}
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-1.5">
                  <StatusChip status={lead.status} tone={statusTone[lead.status] ?? "neutral"} />
                  {lead.escalated && <StatusChip status="escalated" tone="destructive" />}
                </div>
              </TableCell>
              <TableCell>{leadPhoneLabel(lead)}</TableCell>
              <TableCell>
                <ChevronRight className="size-4 text-muted-foreground" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

const KANBAN_COLUMNS: { status: LeadStatus; label: string }[] = [
  { status: "open", label: "Open" },
  { status: "contacted", label: "Contacted" },
  { status: "booked", label: "Booked" },
  { status: "closed", label: "Closed" },
];

function LeadCard({
  lead,
  onClick,
  onDragStart,
}: {
  lead: LeadOut;
  onClick: () => void;
  onDragStart: (e: React.DragEvent) => void;
}) {
  return (
    <Card
      draggable
      onDragStart={onDragStart}
      onClick={onClick}
      className="surface-interactive cursor-grab active:cursor-grabbing"
    >
      <CardContent className="space-y-2 text-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="min-w-0 truncate font-medium">{leadGuestLabel(lead)}</span>
          {lead.lead_temperature && (
            <StatusChip status={lead.lead_temperature} tone={temperatureTone[lead.lead_temperature]} />
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          {lead.properties_discussed.length > 0 ? lead.properties_discussed.join(", ") : "No property discussed"}
        </p>
        <p className="text-xs text-muted-foreground">{leadDatesLabel(lead)}</p>
        {lead.escalated && <StatusChip status="escalated" tone="destructive" />}
      </CardContent>
    </Card>
  );
}

function LeadsKanban({
  leads,
  onCardClick,
  onDropStatus,
}: {
  leads: LeadOut[];
  onCardClick: (lead: LeadOut) => void;
  onDropStatus: (leadId: string, status: LeadStatus) => void;
}) {
  const [dragOverColumn, setDragOverColumn] = useState<LeadStatus | null>(null);

  function leadsForColumn(status: LeadStatus): LeadOut[] {
    return leads
      .filter((lead) => lead.status === status)
      .sort((a, b) => (temperatureRank[a.lead_temperature ?? ""] ?? 3) - (temperatureRank[b.lead_temperature ?? ""] ?? 3));
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {KANBAN_COLUMNS.map((column) => (
        <div
          key={column.status}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOverColumn(column.status);
          }}
          onDragLeave={() => setDragOverColumn((current) => (current === column.status ? null : current))}
          onDrop={(e) => {
            e.preventDefault();
            setDragOverColumn(null);
            const leadId = e.dataTransfer.getData("text/plain");
            if (leadId) onDropStatus(leadId, column.status);
          }}
          className={cn(
            "flex flex-col gap-3 rounded-lg border border-dashed p-3 transition-colors",
            dragOverColumn === column.status ? "border-ring bg-accent" : "border-transparent"
          )}
        >
          <div className="flex items-center justify-between px-1">
            <h2 className="text-sm font-medium">{column.label}</h2>
            <span className="text-xs text-muted-foreground">{leadsForColumn(column.status).length}</span>
          </div>
          <div className="flex flex-col gap-2">
            {leadsForColumn(column.status).map((lead) => (
              <LeadCard
                key={lead.id}
                lead={lead}
                onClick={() => onCardClick(lead)}
                onDragStart={(e) => {
                  e.dataTransfer.setData("text/plain", lead.id);
                  e.dataTransfer.effectAllowed = "move";
                }}
              />
            ))}
            {leadsForColumn(column.status).length === 0 && (
              <p className="px-1 text-xs text-muted-foreground">No leads.</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function LeadsPageContent() {
  const searchParams = useSearchParams();
  const initialStatus = searchParams.get("status") ?? "all";

  const { startDateISO, endDateISO } = useDateRange();
  const { data: leads, loading, refetch } = useAsync(
    () => api.leads.list({ startDate: startDateISO, endDate: endDateISO }),
    [startDateISO, endDateISO]
  );
  const [statusFilter, setStatusFilter] = useState<string>(initialStatus);
  const [showEmpty, setShowEmpty] = useState(false);
  const [editing, setEditing] = useState<LeadOut | null>(null);
  const [temperature, setTemperature] = useState<string>("warm");
  const [status, setStatus] = useState<LeadStatus>("open");
  const [nextFollowUp, setNextFollowUp] = useState("");
  const [summary, setSummary] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [view, setView] = useState<"table" | "board">("table");

  const statusFilteredLeads = (leads ?? []).filter((lead) => statusFilter === "all" || lead.status === statusFilter);
  const contentLeads = statusFilteredLeads
    .filter((lead) => !isEmptyLead(lead))
    .sort((a, b) => {
      const rankDiff = (temperatureRank[a.lead_temperature ?? ""] ?? 3) - (temperatureRank[b.lead_temperature ?? ""] ?? 3);
      if (rankDiff !== 0) return rankDiff;
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
  const emptyLeads = statusFilteredLeads.filter(isEmptyLead);

  function openEdit(lead: LeadOut) {
    setEditing(lead);
    setTemperature(lead.lead_temperature ?? "warm");
    setStatus((lead.status as LeadStatus) ?? "open");
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
        status,
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

  // Deliberately a separate function from handleSave, not a shared helper
  // that both call into -- a drag-drop should only ever be able to send
  // {status}, and keeping this code path structurally independent of the
  // edit-dialog's temperature+status bundle makes it impossible for a
  // future edit here to accidentally start sending lead_temperature too.
  // The backend's PATCH /leads/{id} uses exclude_unset (see
  // backend/app/api/v1/leads.py), so omitting lead_temperature from this
  // payload leaves it completely untouched server-side -- this is not a
  // partial/best-effort safety measure, it's the same mechanism the
  // existing edit-dialog's update already relies on.
  async function handleStatusDrop(leadId: string, newStatus: LeadStatus) {
    const lead = leads?.find((l) => l.id === leadId);
    if (!lead || lead.status === newStatus) return;
    try {
      await api.leads.update(leadId, { status: newStatus });
      toast.success(`Moved to ${newStatus}`);
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update lead status");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">Leads</h1>
          <p className="text-sm text-muted-foreground">Booking enquiries qualified by the Lead Agent</p>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-1 rounded-lg border p-0.5">
            <Button
              variant={view === "table" ? "secondary" : "ghost"}
              size="icon-sm"
              aria-label="Table view"
              onClick={() => setView("table")}
            >
              <Table2 className="size-4" />
            </Button>
            <Button
              variant={view === "board" ? "secondary" : "ghost"}
              size="icon-sm"
              aria-label="Board view"
              onClick={() => setView("board")}
            >
              <LayoutGrid className="size-4" />
            </Button>
          </div>
          {view === "table" && (
            <Select value={statusFilter} onValueChange={(v) => v && setStatusFilter(v)}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <DateRangePicker />
        </div>
      </div>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : view === "board" ? (
        (leads ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No leads yet — they appear here once your portfolio's lead intake number starts receiving calls.
          </p>
        ) : (
          <LeadsKanban leads={leads ?? []} onCardClick={openEdit} onDropStatus={handleStatusDrop} />
        )
      ) : statusFilteredLeads.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {leads && leads.length > 0
            ? "No leads match this status filter."
            : "No leads yet — they appear here once your portfolio's lead intake number starts receiving calls."}
        </p>
      ) : (
        <div className="space-y-4">
          {contentLeads.length > 0 ? (
            <LeadsTable leads={contentLeads} onRowClick={openEdit} />
          ) : (
            <p className="text-sm text-muted-foreground">No leads with captured info yet for this filter.</p>
          )}

          {emptyLeads.length > 0 && (
            <div className="space-y-3 border-t pt-4">
              <div className="flex items-center gap-2">
                <Switch id="show-empty-leads" checked={showEmpty} onCheckedChange={setShowEmpty} />
                <Label htmlFor="show-empty-leads" className="text-sm text-muted-foreground">
                  Show {emptyLeads.length} lead{emptyLeads.length === 1 ? "" : "s"} with no captured info
                </Label>
              </div>
              {showEmpty && <LeadsTable leads={emptyLeads} muted onRowClick={openEdit} />}
            </div>
          )}
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
              <Label>Status</Label>
              <Select value={status} onValueChange={(v) => v && setStatus(v as LeadStatus)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUSES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
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

export default function LeadsPage() {
  // useSearchParams requires a Suspense boundary -- see Next.js docs (this
  // route is behind auth/client-rendered anyway, but this is the documented
  // pattern to avoid de-opting the whole tree above it to client rendering).
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <LeadsPageContent />
    </Suspense>
  );
}
