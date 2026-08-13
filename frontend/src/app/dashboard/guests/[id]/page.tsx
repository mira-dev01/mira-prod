"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { BedDouble, Languages, Phone, Plus, Trash2, UserRound, Wallet } from "lucide-react";
import { toast } from "sonner";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ListRow, ListRowHeader } from "@/components/ui/list-row";
import { Skeleton } from "@/components/ui/skeleton";
import { StatCard } from "@/components/stat-card";
import { StatusChip, type StatusTone } from "@/components/status-chip";
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
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Avatar size="lg">
            <AvatarFallback>{guestInitials(detail.name) ?? <UserRound className="size-5" />}</AvatarFallback>
          </Avatar>
          <div>
            <h1 className="page-title">{isTest ? "Browser test" : detail.name ?? "Unknown guest"}</h1>
            <p className="text-sm text-muted-foreground">{isTest ? "browser-test" : detail.phone}</p>
          </div>
        </div>
        <Button variant="outline" render={<Link href="/dashboard/guests" />}>
          Back to guests
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard icon={BedDouble} label="Total stays" value={detail.total_stays} />
        <StatCard icon={Wallet} label="Lifetime revenue" value={`₹${detail.lifetime_revenue.toLocaleString("en-IN")}`} />
        <StatCard
          icon={Languages}
          label="Preferred language"
          value={detail.preferred_language ? capitalize(detail.preferred_language) : undefined}
        />
        <StatCard
          icon={Phone}
          label="Last call"
          value={detail.last_call_at ? new Date(detail.last_call_at).toLocaleDateString() : undefined}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Previous stays &amp; conversations</CardTitle>
            </CardHeader>
            <CardContent>
              {detail.conversation_summaries.length === 0 ? (
                <p className="text-sm text-muted-foreground">No past conversations yet.</p>
              ) : (
                <div className="space-y-0">
                  {[...detail.conversation_summaries].reverse().map((entry, i) => (
                    <ListRow key={i}>
                      <ListRowHeader>
                        <span className="text-sm font-medium">{entry.property_name ?? "Portfolio-wide"}</span>
                        {entry.lead_temperature && (
                          <Badge variant="outline" className="capitalize">
                            {entry.lead_temperature}
                          </Badge>
                        )}
                      </ListRowHeader>
                      <p className="text-sm text-muted-foreground">{entry.summary}</p>
                      <p className="text-xs text-muted-foreground">
                        {entry.date ? new Date(entry.date).toLocaleDateString() : "—"}
                      </p>
                    </ListRow>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Recent calls</CardTitle>
            </CardHeader>
            <CardContent>
              {detail.recent_calls.length === 0 ? (
                <p className="text-sm text-muted-foreground">No calls yet.</p>
              ) : (
                <div className="space-y-0">
                  {detail.recent_calls.map((call) => (
                    <ListRow key={call.id} interactive onClick={() => router.push(`/dashboard/calls/${call.id}`)}>
                      <ListRowHeader>
                        <span className="text-sm font-medium">{call.property_name ?? "Portfolio-wide"}</span>
                        <StatusChip status={call.status} tone={callStatusTone[call.status] ?? "neutral"} />
                      </ListRowHeader>
                      {call.ai_summary && (
                        <p className="text-sm text-muted-foreground">{call.ai_summary.conversation_summary}</p>
                      )}
                      <p className="text-xs text-muted-foreground">
                        {call.started_at ? new Date(call.started_at).toLocaleString() : "—"}
                      </p>
                    </ListRow>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          {(detail.last_outcome || detail.last_follow_up) && (
            <Card>
              <CardHeader>
                <CardTitle>Status</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
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
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Preferences</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {prefEntries.length === 0 && (
                <p className="text-sm text-muted-foreground">No preferences recorded yet.</p>
              )}
              {prefEntries.map((entry, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input
                    placeholder="e.g. room preference"
                    value={entry.key}
                    onChange={(e) =>
                      setPrefEntries((prev) => prev.map((p, j) => (j === i ? { ...p, key: e.target.value } : p)))
                    }
                    className="w-2/5"
                  />
                  <Input
                    placeholder="e.g. high floor, away from elevator"
                    value={entry.value}
                    onChange={(e) =>
                      setPrefEntries((prev) => prev.map((p, j) => (j === i ? { ...p, value: e.target.value } : p)))
                    }
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
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
                onClick={() => setPrefEntries((prev) => [...prev, { key: "", value: "" }])}
              >
                <Plus className="size-3.5" />
                Add preference
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Edit guest</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSave} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="guest-name">Name</Label>
                  <Input id="guest-name" value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="guest-notes">Notes</Label>
                  <DictationTextarea id="guest-notes" value={notes} onValueChange={setNotes} />
                </div>
                <Button type="submit" disabled={submitting} className="w-full">
                  {submitting ? "Saving…" : "Save"}
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
