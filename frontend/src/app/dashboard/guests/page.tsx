"use client";

import { useState } from "react";
import { Drawer } from "@base-ui/react/drawer";
import { Phone, UserRound, X } from "lucide-react";
import { toast } from "sonner";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ListRow, ListRowHeader } from "@/components/ui/list-row";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip } from "@/components/status-chip";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { DateRangePicker } from "@/components/date-range-picker";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api, ApiError } from "@/lib/api";
import { cn, isBrowserTestIdentity } from "@/lib/utils";
import type { GuestProfileOut } from "@/lib/types";

function guestInitials(name: string | null): string | null {
  const trimmed = name?.trim();
  if (!trimmed) return null;
  const parts = trimmed.split(/\s+/);
  const initials = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2);
  return initials.toUpperCase();
}

// Real fields already fetched (CallSessionOut has no sentiment/intent) --
// same tone mapping as calls/page.tsx, kept local since this is a
// coincidentally-shared value vocabulary, not a shared component.
const callStatusTone: Record<string, "live" | "progress" | "destructive" | "neutral"> = {
  completed: "live",
  active: "progress",
  in_progress: "progress",
  escalated: "destructive",
  failed: "destructive",
  missed: "destructive",
};

function GuestDrawer({
  guestId,
  open,
  onOpenChange,
  onSaved,
}: {
  guestId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const { data: detail, loading } = useAsync(
    () => (guestId ? api.guests.detail(guestId) : Promise.resolve(null)),
    [guestId]
  );
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  // Seed the edit form once per newly-loaded guest, not on every render --
  // detail is refetched (new object identity) after a save too, and we
  // don't want that to stomp on further in-progress edits.
  if (detail && loadedFor !== detail.id) {
    setName(detail.name ?? "");
    setNotes(detail.notes ?? "");
    setLoadedFor(detail.id);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!detail) return;
    setSubmitting(true);
    try {
      await api.guests.update(detail.id, { name, notes });
      toast.success("Guest updated");
      onSaved();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update guest");
    } finally {
      setSubmitting(false);
    }
  }

  const isTest = detail ? isBrowserTestIdentity(detail.phone) : false;

  return (
    <Drawer.Root open={open} onOpenChange={onOpenChange} swipeDirection="right">
      <Drawer.Portal>
        <Drawer.Backdrop className="fixed inset-0 z-50 bg-black/40 transition-opacity duration-200 data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Drawer.Viewport className="fixed inset-0 z-50 flex items-stretch justify-end">
          <Drawer.Popup
            className={cn(
              "flex h-full w-full max-w-md flex-col gap-4 overflow-y-auto bg-card p-6 outline-none",
              "transition-transform duration-200 [transform:translateX(var(--drawer-swipe-movement-x))]",
              "data-starting-style:translate-x-full data-ending-style:translate-x-full"
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3">
                <Avatar size="lg">
                  <AvatarFallback>
                    {detail ? guestInitials(detail.name) ?? <UserRound className="size-5" /> : null}
                  </AvatarFallback>
                </Avatar>
                <div className="min-w-0">
                  <Drawer.Title className="truncate text-base font-medium">
                    {isTest ? "Browser test" : detail?.name ?? "Unknown guest"}
                  </Drawer.Title>
                  <p className="truncate text-sm text-muted-foreground">
                    {isTest ? "browser-test" : detail?.phone ?? ""}
                  </p>
                </div>
              </div>
              <Drawer.Close
                aria-label="Close"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
              >
                <X className="size-4" />
              </Drawer.Close>
            </div>

            {loading || !detail ? (
              <div className="space-y-3">
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-32 w-full" />
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Total stays</p>
                    <p className="text-lg font-semibold">{detail.total_stays}</p>
                  </div>
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Lifetime revenue</p>
                    <p className="text-lg font-semibold">₹{detail.lifetime_revenue.toLocaleString("en-IN")}</p>
                  </div>
                </div>

                {(detail.preferred_language || detail.last_outcome || detail.last_follow_up) && (
                  <div className="space-y-2 rounded-lg border p-3 text-sm">
                    {detail.preferred_language && (
                      <p>
                        <span className="text-muted-foreground">Prefers:</span> {detail.preferred_language}
                      </p>
                    )}
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
                  </div>
                )}

                {detail.conversation_summaries.length > 0 && (
                  <div className="space-y-2">
                    <h3 className="text-sm font-medium">Past conversations</h3>
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
                          <p className="text-xs text-muted-foreground">{entry.summary}</p>
                          <p className="text-xs text-muted-foreground">
                            {entry.date ? new Date(entry.date).toLocaleDateString() : "—"}
                          </p>
                        </ListRow>
                      ))}
                    </div>
                  </div>
                )}

                <div className="space-y-2">
                  <h3 className="text-sm font-medium">Recent calls</h3>
                  {detail.recent_calls.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No calls yet.</p>
                  ) : (
                    <div className="space-y-0">
                      {detail.recent_calls.map((call) => (
                        <ListRow key={call.id}>
                          <ListRowHeader>
                            <span className="text-sm font-medium">{call.property_name ?? "Portfolio-wide"}</span>
                            <StatusChip status={call.status} tone={callStatusTone[call.status] ?? "neutral"} />
                          </ListRowHeader>
                          {call.ai_summary && (
                            <p className="text-xs text-muted-foreground">{call.ai_summary}</p>
                          )}
                          <p className="text-xs text-muted-foreground">
                            {call.started_at ? new Date(call.started_at).toLocaleString() : "—"}
                          </p>
                        </ListRow>
                      ))}
                    </div>
                  )}
                </div>

                <form onSubmit={handleSave} className="space-y-4 border-t pt-4">
                  <div className="space-y-2">
                    <Label htmlFor="guest-name">Name</Label>
                    <Input id="guest-name" value={name} onChange={(e) => setName(e.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="guest-notes">Notes</Label>
                    <Textarea id="guest-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
                  </div>
                  <Button type="submit" disabled={submitting} className="w-full">
                    Save
                  </Button>
                </form>
              </>
            )}
          </Drawer.Popup>
        </Drawer.Viewport>
      </Drawer.Portal>
    </Drawer.Root>
  );
}

export default function GuestsPage() {
  const { startDateISO, endDateISO } = useDateRange();
  const { data: guests, loading, refetch } = useAsync(
    () => api.guests.list({ startDate: startDateISO, endDate: endDateISO }),
    [startDateISO, endDateISO]
  );
  const [openGuestId, setOpenGuestId] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">Guests</h1>
          <p className="text-sm text-muted-foreground">Guest CRM built from past calls and stays</p>
        </div>
        <DateRangePicker />
      </div>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : !guests || guests.length === 0 ? (
        <p className="text-sm text-muted-foreground">No guest profiles yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>Total stays</TableHead>
              <TableHead className="w-20" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {guests.map((guest) => (
              <TableRow key={guest.id} className="cursor-pointer" onClick={() => setOpenGuestId(guest.id)}>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <Avatar size="sm">
                      <AvatarFallback>
                        {guestInitials(guest.name) ?? <UserRound className="size-3.5" />}
                      </AvatarFallback>
                    </Avatar>
                    {guest.name ?? "—"}
                  </div>
                </TableCell>
                <TableCell>
                  {isBrowserTestIdentity(guest.phone) ? <Badge variant="outline">Browser test</Badge> : guest.phone}
                </TableCell>
                <TableCell>{guest.total_stays}</TableCell>
                <TableCell>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpenGuestId(guest.id);
                    }}
                  >
                    <Phone className="size-3.5" />
                    View
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <GuestDrawer
        guestId={openGuestId}
        open={!!openGuestId}
        onOpenChange={(open) => !open && setOpenGuestId(null)}
        onSaved={() => {
          setOpenGuestId(null);
          refetch();
        }}
      />
    </div>
  );
}
