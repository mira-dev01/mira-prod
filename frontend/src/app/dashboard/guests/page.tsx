"use client";

import { useRouter } from "next/navigation";
import { UserRound } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Cell, Sheet } from "@/components/ui/sheet";
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
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <h1 className="page-title">Guests</h1>
        <DateRangePicker />
      </div>

      <Sheet>
        <Cell className="gap-0 p-0">
          {loading ? (
            <Skeleton className="m-5 h-64" />
          ) : !guests || guests.length === 0 ? (
            <p className="p-5 text-sm text-muted-foreground">No guest profiles yet.</p>
          ) : (
            <div className="overflow-x-auto px-3 py-2">
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
                      <TableCell className="tabular-nums">{guest.total_stays}</TableCell>
                      <TableCell>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            router.push(`/dashboard/guests/${guest.id}`);
                          }}
                        >
                          View
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </Cell>
      </Sheet>
    </div>
  );
}
