"use client";

import { useRouter } from "next/navigation";
import { Phone, UserRound } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DateRangePicker } from "@/components/date-range-picker";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { isBrowserTestIdentity } from "@/lib/utils";

function guestInitials(name: string | null): string | null {
  const trimmed = name?.trim();
  if (!trimmed) return null;
  const parts = trimmed.split(/\s+/);
  const initials = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0].slice(0, 2);
  return initials.toUpperCase();
}

export default function GuestsPage() {
  const router = useRouter();
  const { startDateISO, endDateISO } = useDateRange();
  const { data: guests, loading } = useAsync(
    () => api.guests.list({ startDate: startDateISO, endDate: endDateISO }),
    [startDateISO, endDateISO]
  );

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
              <TableRow
                key={guest.id}
                className="cursor-pointer"
                onClick={() => router.push(`/dashboard/guests/${guest.id}`)}
              >
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
                      router.push(`/dashboard/guests/${guest.id}`);
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
    </div>
  );
}
