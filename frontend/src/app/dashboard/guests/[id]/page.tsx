"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Plus, Trash2, UserRound } from "lucide-react";
import { toast } from "sonner";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CardContent, CardHeader } from "@/components/ui/card";
import { Cell, CellCard, CellTitle, Sheet, SubField } from "@/components/ui/sheet";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { ListRow, ListRowHeader } from "@/components/ui/list-row";
import { Skeleton } from "@/components/ui/skeleton";
import { MetricCell } from "@/components/metric-cell";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { useLeadLabels } from "@/lib/leads";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { isBrowserTestIdentity } from "@/lib/utils";

// Same call-status vocabulary as calls/[id]/page.tsx and the old
// guests/page.tsx drawer -- kept local per the existing per-page convention
// rather than extracted, since each page's status set has drifted slightly.
const callStatusTone: Record<string, StatusTone> = {
  completed: "live",
  active: "progress",
  in_progress: "progress",
  escalated: "destructive",
  failed: "destructive",
  missed: "destructive",
};

function capitalize(value: string): string {
  return value.length === 0 ? value : value[0].toUpperCase() + value.slice(1);
}

function guestInitials(name: string | null): string | null {
  const trimmed = name?.trim();
  if (!trimmed) return null;
  const parts = trimmed.split(/\s+/);
  const initials = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2);
  return initials.toUpperCase();
}

// preferences is a free-form JSONB dict with no fixed schema anywhere in the
// backend (GuestProfile.preferences), so the only safe editor for it is a
// key/value list rather than assuming specific fields exist.
type PrefEntry = { key: string; value: string };

function prefsToEntries(prefs: Record<string, unknown>): PrefEntry[] {
  return Object.entries(prefs).map(([key, value]) => ({ key, value: String(value) }));
}

function entriesToPrefs(entries: PrefEntry[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const { key, value } of entries) {
    const trimmedKey = key.trim();
    if (trimmedKey) out[trimmedKey] = value;
  }
  return out;
}

export default function GuestProfilePage() {
  const leadLabels = useLeadLabels();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { data: detail, loading, refetch } = useAsync(() => api.guests.detail(params.id), [params.id]);

  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [prefEntries, setPrefEntries] = useState<PrefEntry[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  // Seed the edit form once per newly-loaded guest, same pattern as the
  // former GuestDrawer -- detail is refetched (new object identity) after a
  // save too, and we don't want that to stomp on further in-progress edits.
  if (detail && loadedFor !== detail.id) {
    setName(detail.name ?? "");
    setNotes(detail.notes ?? "");
    setPrefEntries(prefsToEntries(detail.preferences));
    setLoadedFor(detail.id);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!detail) return;
    setSubmitting(true);
    try {
      await api.guests.update(detail.id, { name, notes, preferences: entriesToPrefs(prefEntries) });
      toast.success("Guest updated");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update guest");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">Guest not found.</p>
        <Button variant="outline" render={<Link href="/dashboard/guests" />}>
          Back to guests
        </Button>
      </div>
    );
  }

  const isTest = isBrowserTestIdentity(detail.phone);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <Avatar size="lg">
            <AvatarFallback>{guestInitials(detail.name) ?? <UserRound className="size-5" />}</AvatarFallback>
          </Avatar>
          <div className="min-w-0">
            <h1 className="page-title">{isTest ? "Browser test" : detail.name ?? "Unknown guest"}</h1>
            <p className="text-sm text-muted-foreground">{isTest ? "browser-test" : detail.phone}</p>
          </div>
        </div>
        <Button variant="outline" render={<Link href="/dashboard/guests" />}>
          Back to guests
        </Button>
      </div>

      <Sheet>
        <MetricCell label="Total stays" value={detail.total_stays} />
        <MetricCell label="Lifetime revenue" value={`₹${detail.lifetime_revenue.toLocaleString("en-IN")}`} accent />
        <MetricCell
          label="Preferred language"
          value={detail.preferred_language ? capitalize(detail.preferred_language) : "—"}
          muted={!detail.preferred_language}
        />
        <MetricCell
          label="Last call"
          value={detail.last_call_at ? new Date(detail.last_call_at).toLocaleDateString() : "—"}
          muted={!detail.last_call_at}
        />

        {(detail.last_outcome || detail.last_follow_up) && (
          <Cell className="flex-row flex-wrap items-center gap-x-8 gap-y-2 py-4 text-sm">
            {detail.last_outcome && (
              <p>
                <span className="text-muted-foreground">Last outcome:</span> {detail.last_outcome}
              </p>
            )}
            {detail.last_follow_up && (
              <p>
                <span className="text-muted-foreground">Follow up:</span> {detail.last_follow_up}
              </p>
            )}
          </Cell>
        )}

        <CellCard className="md:col-span-6">
          <CardHeader>
            <CellTitle>Previous stays &amp; conversations</CellTitle>
          </CardHeader>
          <CardContent>
            {detail.conversation_summaries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No past conversations yet.</p>
            ) : (
              <div>
                {[...detail.conversation_summaries].reverse().map((entry, i) => (
                  <ListRow key={i} variant="boxed">
                    <ListRowHeader>
                      <span className="text-sm font-semibold">{entry.property_name ?? "Portfolio-wide"}</span>
                      <Badge variant="outline">{leadLabels.label(entry.lead_temperature)}</Badge>
                    </ListRowHeader>
                    <p className="text-sm text-muted-foreground">{entry.summary}</p>
                    <p className="text-[13px] text-muted-foreground">
                      {entry.date ? new Date(entry.date).toLocaleDateString() : "—"}
                    </p>
                  </ListRow>
                ))}
              </div>
            )}
          </CardContent>
        </CellCard>

        <CellCard className="md:col-span-6">
          <CardHeader>
            <CellTitle>Recent calls</CellTitle>
          </CardHeader>
          <CardContent>
            {detail.recent_calls.length === 0 ? (
              <p className="text-sm text-muted-foreground">No calls yet.</p>
            ) : (
              <div>
                {detail.recent_calls.map((call) => (
                  <ListRow key={call.id} variant="boxed" interactive onClick={() => router.push(`/dashboard/calls/${call.id}`)}>
                    <ListRowHeader>
                      <span className="text-sm font-semibold">{call.property_name ?? "Portfolio-wide"}</span>
                      <StatusChip status={call.status} tone={callStatusTone[call.status] ?? "neutral"} />
                    </ListRowHeader>
                    {call.ai_summary && (
                      <p className="text-sm text-muted-foreground">{call.ai_summary.conversation_summary}</p>
                    )}
                    <p className="text-[13px] text-muted-foreground">
                      {call.started_at ? new Date(call.started_at).toLocaleString() : "—"}
                    </p>
                  </ListRow>
                ))}
              </div>
            )}
          </CardContent>
        </CellCard>

        <Cell className="md:col-span-6">
          <CellTitle>Preferences</CellTitle>
          {prefEntries.length === 0 && <p className="text-sm text-muted-foreground">No preferences recorded yet.</p>}
          {prefEntries.map((entry, i) => (
            <div key={i} className="flex items-center gap-2">
              <Input
                placeholder="e.g. room preference"
                aria-label="Preference"
                value={entry.key}
                onChange={(e) =>
                  setPrefEntries((prev) => prev.map((p, j) => (j === i ? { ...p, key: e.target.value } : p)))
                }
                className="w-2/5"
              />
              <Input
                placeholder="e.g. high floor, away from elevator"
                aria-label="Value"
                value={entry.value}
                onChange={(e) =>
                  setPrefEntries((prev) => prev.map((p, j) => (j === i ? { ...p, value: e.target.value } : p)))
                }
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Remove preference"
                onClick={() => setPrefEntries((prev) => prev.filter((_, j) => j !== i))}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => setPrefEntries((prev) => [...prev, { key: "", value: "" }])}
          >
            <Plus className="size-3.5" />
            Add preference
          </Button>
        </Cell>

        <Cell className="md:col-span-6">
          <CellTitle>Edit guest</CellTitle>
          <form onSubmit={handleSave} className="space-y-4">
            <SubField label="Name" htmlFor="guest-name">
              <Input id="guest-name" value={name} onChange={(e) => setName(e.target.value)} />
            </SubField>
            <SubField label="Notes" htmlFor="guest-notes">
              <DictationTextarea id="guest-notes" value={notes} onValueChange={setNotes} />
            </SubField>
            <Button type="submit" variant="cta" disabled={submitting}>
              {submitting ? "Saving…" : "Save"}
            </Button>
          </form>
        </Cell>
      </Sheet>
    </div>
  );
}
