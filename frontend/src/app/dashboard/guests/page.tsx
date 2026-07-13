"use client";

import { useState } from "react";
import { UserRound } from "lucide-react";
import { toast } from "sonner";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { DateRangePicker } from "@/components/date-range-picker";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api, ApiError } from "@/lib/api";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { GuestProfileOut } from "@/lib/types";

function guestInitials(name: string | null): string | null {
  const trimmed = name?.trim();
  if (!trimmed) return null;
  const parts = trimmed.split(/\s+/);
  const initials = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2);
  return initials.toUpperCase();
}

export default function GuestsPage() {
  const { startDateISO, endDateISO } = useDateRange();
  const { data: guests, loading, refetch } = useAsync(
    () => api.guests.list({ startDate: startDateISO, endDate: endDateISO }),
    [startDateISO, endDateISO]
  );
  const [editing, setEditing] = useState<GuestProfileOut | null>(null);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function openEdit(guest: GuestProfileOut) {
    setEditing(guest);
    setName(guest.name ?? "");
    setNotes(guest.notes ?? "");
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setSubmitting(true);
    try {
      await api.guests.update(editing.id, { name, notes });
      toast.success("Guest updated");
      setEditing(null);
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update guest");
    } finally {
      setSubmitting(false);
    }
  }

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
              <TableRow key={guest.id}>
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
                  <Button variant="outline" size="sm" onClick={() => openEdit(guest)}>
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
              Edit guest — {editing && isBrowserTestIdentity(editing.phone) ? "Browser test" : editing?.phone}
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSave} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="guest-name">Name</Label>
              <Input id="guest-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="guest-notes">Notes</Label>
              <Textarea id="guest-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
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
