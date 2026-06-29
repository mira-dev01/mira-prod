"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { cn, isBrowserTestIdentity } from "@/lib/utils";
import type { LeadOut } from "@/lib/types";

const TEMPERATURES = ["hot", "warm", "cold"] as const;

const temperatureVariant: Record<string, "destructive" | "outline"> = {
  hot: "destructive",
  warm: "outline",
  cold: "outline",
};

const temperatureClassName: Record<string, string> = {
  warm: "badge-status-pending",
};

export default function LeadsPage() {
  const { data: leads, loading, refetch } = useAsync(() => api.leads.list(), []);
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
      <div>
        <h1 className="page-title">Leads</h1>
        <p className="text-sm text-muted-foreground">Booking enquiries qualified by the Lead Agent</p>
      </div>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : !leads || leads.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No leads yet — they appear here once your portfolio&apos;s lead intake number starts receiving calls.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Guest</TableHead>
              <TableHead>Property</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>Dates</TableHead>
              <TableHead>Guests</TableHead>
              <TableHead>Budget</TableHead>
              <TableHead>Temperature</TableHead>
              <TableHead>Follow-up</TableHead>
              <TableHead>Escalated</TableHead>
              <TableHead className="w-20" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {leads.map((lead) => (
              <TableRow key={lead.id}>
                <TableCell>
                  {isBrowserTestIdentity(lead.phone) ? <Badge variant="outline">Browser test</Badge> : lead.guest_name ?? "Unknown"}
                </TableCell>
                <TableCell>
                  {lead.properties_discussed.length > 0 ? lead.properties_discussed.join(", ") : "—"}
                </TableCell>
                <TableCell>
                  {isBrowserTestIdentity(lead.phone) ? "—" : lead.phone ?? "—"}
                </TableCell>
                <TableCell>
                  {lead.check_in && lead.check_out ? `${lead.check_in} → ${lead.check_out}` : "—"}
                </TableCell>
                <TableCell>{lead.num_guests ?? "—"}</TableCell>
                <TableCell>{lead.budget ? `₹${lead.budget.toLocaleString("en-IN")}` : "—"}</TableCell>
                <TableCell>
                  {lead.lead_temperature ? (
                    <Badge
                      variant={temperatureVariant[lead.lead_temperature] ?? "outline"}
                      className={cn("capitalize", temperatureClassName[lead.lead_temperature])}
                    >
                      {lead.lead_temperature}
                    </Badge>
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell className="max-w-[160px] truncate">{lead.next_follow_up ?? "—"}</TableCell>
                <TableCell>{lead.escalated ? <Badge variant="destructive">Yes</Badge> : "No"}</TableCell>
                <TableCell>
                  <Button variant="outline" size="sm" onClick={() => openEdit(lead)}>
                    Edit
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
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
