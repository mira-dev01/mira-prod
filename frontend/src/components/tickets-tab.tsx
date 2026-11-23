"use client";

import { useEffect, useRef, useState } from "react";
import { Bell, BellOff, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Cell, Sheet, Toolbar } from "@/components/ui/sheet";
import { SegmentedChoice } from "@/components/segmented-choice";
import { TicketCard } from "@/components/ticket-card";
import { TicketTranscriptPanel } from "@/components/ticket-transcript-panel";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { useBookingActivity } from "@/lib/booking-activity-context";
import { matchesSearch } from "@/lib/utils";
import type { TicketOut } from "@/lib/types";

/**
 * Leads -> "Tickets": everything Mira told a guest would happen after the call (and every call that did not connect),
 * until the host marks it done. Each shows when it was raised, how long it has waited, the property, and the call
 * behind it with the escalation lines highlighted. A ticket for a guest who is booked is also on that guest's row in
 * Checked-in: it is the same ticket, so resolving it in either place clears it in both. A new one raises a banner,
 * and a ping when the ringer is on (booking-activity-context.tsx).
 */
export function TicketsTab() {
  const [view, setView] = useState<"open" | "resolved">("open");
  const [search, setSearch] = useState("");
  const [viewing, setViewing] = useState<TicketOut | null>(null);
  const { data: tickets, loading, refetch } = useAsync(() => api.tickets.list(view), [view]);
  const { version, refresh, soundOn, setSoundOn } = useBookingActivity();

  // A ticket raised (or changed) anywhere in the dashboard: read the list again.
  const lastVersion = useRef(version);
  useEffect(() => {
    if (lastVersion.current === version) return;
    lastVersion.current = version;
    refetch();
  }, [version, refetch]);

  function changed() {
    refetch();
    void refresh();
  }

  const visible = (tickets ?? []).filter((ticket) =>
    matchesSearch(search, [ticket.summary, ticket.detail, ticket.property_name, ticket.guest_name, ticket.guest_phone, ticket.kind])
  );

  return (
    <>
      <Sheet>
        <Toolbar className="justify-between">
          <div className="flex flex-wrap items-center gap-3">
            <SegmentedChoice
              label="Show tickets"
              value={view}
              onChange={setView}
              options={[
                { value: "open", label: "Open" },
                { value: "resolved", label: "Done" },
              ]}
            />
            {tickets && tickets.length > 0 && (
              <div className="w-full sm:w-64">
                <Input placeholder="Search tickets…" value={search} onChange={(e) => setSearch(e.target.value)} leadingIcon={<Search />} />
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="icon-sm"
              variant="outline"
              aria-label={soundOn ? "Turn the new-ticket sound off" : "Turn the new-ticket sound on"}
              title={soundOn ? "Sound on" : "Sound off"}
              aria-pressed={soundOn}
              onClick={() => setSoundOn(!soundOn)}
            >
              {soundOn ? <Bell className="size-4" /> : <BellOff className="size-4" />}
            </Button>
            <Button size="icon-sm" variant="outline" aria-label="Refresh" title="Refresh" onClick={changed}>
              <RefreshCw className="size-4" />
            </Button>
          </div>
        </Toolbar>

        <Cell>
          {loading && !tickets ? (
            <Skeleton className="h-48 w-full" />
          ) : !tickets || tickets.length === 0 ? (
            <p className="text-sm text-muted-foreground">{view === "open" ? "Nothing waiting on you." : "Nothing marked done yet."}</p>
          ) : visible.length === 0 ? (
            <p className="text-sm text-muted-foreground">No tickets match your search.</p>
          ) : (
            <div>
              {visible.map((ticket) => (
                <TicketCard key={ticket.id} ticket={ticket} onChanged={changed} onTranscript={setViewing} />
              ))}
            </div>
          )}
        </Cell>
      </Sheet>

      <TicketTranscriptPanel
        callSessionId={viewing?.call_session_id ?? null}
        guestName={viewing?.guest_name ?? null}
        onOpenChange={(open) => !open && setViewing(null)}
      />
    </>
  );
}
