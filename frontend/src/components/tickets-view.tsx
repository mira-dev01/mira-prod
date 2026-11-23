"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDownUp, Bell, BellOff, Check, ChevronRight, CircleCheck, Inbox, ListChecks, RefreshCw, Search, Timer } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SpacezStayRequestActions } from "@/components/spacez-stay-request-actions";
import { StatusChip } from "@/components/status-chip";
import { KIND_LABEL, PHASE_LABEL, formatSpan } from "@/components/ticket-card";
import { TicketDetailPanel } from "@/components/ticket-detail-panel";
import { useAsync } from "@/hooks/use-async";
import { useNow } from "@/hooks/use-now";
import { api, ApiError } from "@/lib/api";
import { useBookingActivity } from "@/lib/booking-activity-context";
import { formatLeadTimestamp } from "@/lib/leads";
import { KIND_TONE, SLA_MS, ticketCode, ticketPromises, ticketSubtitle } from "@/lib/tickets";
import { toneDotClassName } from "@/lib/tone";
import { cn, isBrowserTestIdentity, matchesSearch } from "@/lib/utils";
import type { TicketKind, TicketOut } from "@/lib/types";

type TabKey = "open" | "done" | "dismissed";
type ChipKey = "all" | "upcoming" | TicketKind;

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
// What Mira promised comes first, then the follow-ups, then the rest.
const CHIP_ORDER: TicketKind[] = ["promise", "escalation", "photos", "whatsapp", "transfer", "missed_call", "technician", "payment", "request", "stay_request"];

function StatCard({ icon: Icon, label, value, tone }: { icon: typeof Inbox; label: string; value: number | null; tone?: "red" | "green" }) {
  const color = tone === "red" ? "text-destructive" : tone === "green" ? "text-(--status-live)" : undefined;
  return (
    <div className="rounded-xl border border-border bg-card px-5 py-4">
      <p className={cn("flex items-center gap-2 text-sm text-muted-foreground", color)}>
        <Icon className="size-4" />
        {label}
      </p>
      <p className={cn("mt-2 text-3xl leading-none font-semibold tabular-nums", color)}>{value ?? "—"}</p>
    </div>
  );
}

function FilterChip({ active, dot, onClick, className, children }: { active: boolean; dot: string; onClick: () => void; className?: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex h-8 items-center gap-2 rounded-lg border px-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        active ? "border-foreground bg-foreground text-background" : "border-border bg-card text-foreground hover:bg-accent",
        className
      )}
    >
      <span className={cn("size-1.5 rounded-full", active ? "bg-background" : dot)} />
      {children}
    </button>
  );
}

/** The age column: how long it has been open (red once past 24 h) or, for a closed ticket, how long it took. */
function AgeCell({ ticket, now }: { ticket: TicketOut; now: number }) {
  const started = new Date(ticket.created_at).getTime();
  const open = ticket.status === "open";
  const ended = open ? now : ticket.resolved_at ? new Date(ticket.resolved_at).getTime() : started;
  const span = Math.max(0, ended - started);
  const breached = span > SLA_MS;
  return (
    <div className="space-y-1">
      <p className={cn("flex items-center gap-1.5 text-sm font-semibold tabular-nums", open && breached && "text-destructive")}>
        <Timer className="size-3.5" />
        {formatSpan(span)}
      </p>
      <span
        className={cn(
          "inline-block rounded px-1.5 py-0.5 text-xs font-medium",
          breached ? "bg-destructive/10 text-destructive" : "bg-(--status-live)/10 text-(--status-live)"
        )}
      >
        {breached ? "SLA breached" : "Within 24 h"}
      </span>
    </div>
  );
}

function TicketRow({
  ticket,
  now,
  onOpen,
  onChanged,
}: {
  ticket: TicketOut;
  now: number;
  onOpen: (ticket: TicketOut) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const phone = isBrowserTestIdentity(ticket.guest_phone) ? null : ticket.guest_phone;
  const subtitle = ticketSubtitle(ticket);
  const open = ticket.status === "open";
  const breached = open && now - new Date(ticket.created_at).getTime() > SLA_MS;

  async function setStatus(status: "open" | "done") {
    setBusy(true);
    try {
      await api.tickets.setStatus(ticket.id, status);
      toast.success(status === "done" ? `${ticketCode(ticket)} marked done` : `${ticketCode(ticket)} reopened`);
      onChanged();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not update the ticket");
    } finally {
      setBusy(false);
    }
  }

  return (
    <TableRow
      className="cursor-pointer"
      tabIndex={0}
      onClick={() => onOpen(ticket)}
      onKeyDown={(e) => {
        if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onOpen(ticket);
        }
      }}
    >
      {/* the red bar on the left marks a ticket that is past its SLA */}
      <TableCell className={cn("py-3 pl-4 align-middle whitespace-normal", breached && "shadow-[inset_3px_0_0_0_var(--destructive)]")}>
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-xs text-muted-foreground">{ticketCode(ticket)}</span>
            {ticket.booking_phase && ticket.booking_phase !== "checked_out" && (
              <StatusChip status={PHASE_LABEL[ticket.booking_phase]} tone="live" className="normal-case" />
            )}
            <StatusChip status={KIND_LABEL[ticket.kind]} tone={KIND_TONE[ticket.kind]} />
          </div>
          <p className="text-sm leading-snug font-semibold">{ticket.summary}</p>
          {subtitle && <p className="line-clamp-2 text-[13px] leading-snug text-muted-foreground">{subtitle}</p>}
        </div>
      </TableCell>
      <TableCell className="py-3 align-middle whitespace-normal">
        <p className="text-sm font-medium">{ticket.guest_name ?? "Unknown guest"}</p>
        {phone && <p className="font-mono text-[13px] text-muted-foreground">{phone}</p>}
      </TableCell>
      <TableCell className="py-3 align-middle text-sm whitespace-normal">{ticket.property_name ?? "—"}</TableCell>
      <TableCell className="py-3 align-middle">
        <AgeCell ticket={ticket} now={now} />
      </TableCell>
      <TableCell className="py-3 align-middle text-sm whitespace-nowrap text-muted-foreground">{formatLeadTimestamp(ticket.created_at)}</TableCell>
      <TableCell className="py-3 pr-3 align-middle" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-end gap-1">
          {open && ticket.kind === "stay_request" ? (
            // Spacez only: answered with yes / no (which messages the guest), never "done".
            <SpacezStayRequestActions ticketId={ticket.id} onAnswered={onChanged} />
          ) : open ? (
            <Button size="sm" disabled={busy} onClick={() => setStatus("done")}>
              <Check className="size-4" />
              Done
            </Button>
          ) : (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setStatus("open")}>
              Reopen
            </Button>
          )}
          <button
            type="button"
            aria-label={`Open ${ticketCode(ticket)}`}
            className="flex size-6 items-center justify-center rounded text-muted-foreground hover:text-foreground"
            onClick={() => onOpen(ticket)}
          >
            <ChevronRight className="size-4" />
          </button>
        </div>
      </TableCell>
    </TableRow>
  );
}

/**
 * The Tickets page (sidebar): everything Mira told a guest would happen after a call, and every call that did not
 * connect, until the host marks it done. Four numbers on top (open, past the 24 h SLA, promises still owed, done this
 * week), then Open / Done / Dismissed with a search and a filter by kind, oldest open first. Click a row for the full
 * ticket and the call transcript with the escalation lines highlighted. A ticket for a booked guest is also on that
 * guest's row in Leads -> Checked-in: it is the same ticket. A new one raises a banner, and a ping when the ringer is
 * on (booking-activity-context.tsx).
 */
export function TicketsView() {
  const [tab, setTab] = useState<TabKey>("open");
  const [search, setSearch] = useState("");
  const [chip, setChip] = useState<ChipKey>("all");
  const [openId, setOpenId] = useState<string | null>(null);
  const open = useAsync(() => api.tickets.list("open"), []);
  const resolved = useAsync(() => api.tickets.list("resolved"), []);
  const { version, refresh, soundOn, setSoundOn } = useBookingActivity();
  const now = useNow(true, 30_000);

  // A ticket raised (or changed) anywhere in the dashboard: read both lists again.
  const lastVersion = useRef(version);
  const { refetch: refetchOpen } = open;
  const { refetch: refetchResolved } = resolved;
  useEffect(() => {
    if (lastVersion.current === version) return;
    lastVersion.current = version;
    refetchOpen();
    refetchResolved();
  }, [version, refetchOpen, refetchResolved]);

  function changed() {
    refetchOpen();
    refetchResolved();
    void refresh();
  }

  const openList = useMemo(() => open.data ?? [], [open.data]);
  const resolvedList = useMemo(() => resolved.data ?? [], [resolved.data]);
  const done = useMemo(() => resolvedList.filter((t) => t.status === "done"), [resolvedList]);
  const dismissed = useMemo(() => resolvedList.filter((t) => t.status === "dismissed"), [resolvedList]);

  const loaded = open.data !== null && resolved.data !== null;
  const stats = {
    open: openList.length,
    breached: openList.filter((t) => now - new Date(t.created_at).getTime() > SLA_MS).length,
    promises: openList.reduce((sum, t) => sum + ticketPromises(t).length, 0),
    week: done.filter((t) => t.resolved_at && now - new Date(t.resolved_at).getTime() <= WEEK_MS).length,
  };

  const inTab = tab === "open" ? openList : tab === "done" ? done : dismissed;

  // The filter chips: a kind only shows while a ticket of that kind is in this tab, in a fixed order.
  const chips = useMemo(() => {
    const present = new Set(inTab.map((t) => t.kind));
    const kinds = CHIP_ORDER.filter((kind) => present.has(kind));
    return { kinds, upcoming: inTab.some((t) => t.booking_phase === "upcoming") };
  }, [inTab]);
  const activeChip: ChipKey = chip === "all" || (chip === "upcoming" ? chips.upcoming : chips.kinds.includes(chip)) ? chip : "all";

  const visible = useMemo(() => {
    const rows = inTab.filter((t) => {
      if (activeChip === "upcoming" && t.booking_phase !== "upcoming") return false;
      if (activeChip !== "all" && activeChip !== "upcoming" && t.kind !== activeChip) return false;
      return matchesSearch(search, [ticketCode(t), t.summary, t.detail, t.guest_name, t.guest_phone, t.property_name, KIND_LABEL[t.kind]]);
    });
    const at = (t: TicketOut) => new Date(tab === "open" ? t.created_at : (t.resolved_at ?? t.created_at)).getTime();
    // Oldest open first: the one that has waited longest is at the top. Closed ones: the latest closed first.
    return rows.sort((a, b) => (tab === "open" ? at(a) - at(b) : at(b) - at(a)));
  }, [inTab, activeChip, search, tab]);

  const opened = openId ? ([...openList, ...resolvedList].find((t) => t.id === openId) ?? null) : null;
  const count = (n: number) => (
    <span className="rounded-full bg-muted px-1.5 text-[11px] leading-5 font-semibold text-muted-foreground tabular-nums">{n}</span>
  );

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm text-muted-foreground">Escalations raised by Mira during guest calls</p>
          <h1 className="page-title">Tickets</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="icon"
            variant="outline"
            aria-label={soundOn ? "Turn the new-ticket sound off" : "Turn the new-ticket sound on"}
            title={soundOn ? "Sound on" : "Sound off"}
            aria-pressed={soundOn}
            onClick={() => setSoundOn(!soundOn)}
          >
            {soundOn ? <Bell className="size-4" /> : <BellOff className="size-4" />}
          </Button>
          <Button size="icon" variant="outline" aria-label="Refresh" title="Refresh" onClick={changed}>
            <RefreshCw className="size-4" />
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard icon={Inbox} label="Open tickets" value={loaded ? stats.open : null} />
        <StatCard icon={Timer} label="SLA breached (>24 h)" value={loaded ? stats.breached : null} tone="red" />
        <StatCard icon={ListChecks} label="Promises pending" value={loaded ? stats.promises : null} />
        <StatCard icon={CircleCheck} label="Resolved this week" value={loaded ? stats.week : null} tone="green" />
      </div>

      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <Tabs
          value={tab}
          onValueChange={(value) => {
            setTab(value as TabKey);
            setChip("all");
          }}
          className="gap-0"
        >
          <TabsList className="h-12 gap-6 px-5">
            <TabsTrigger value="open">
              Open
              {stats.open > 0 && (
                <span className="inline-flex size-5 items-center justify-center rounded-full bg-primary text-[11px] leading-none font-bold text-primary-foreground tabular-nums">
                  {stats.open > 9 ? "9+" : stats.open}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="done">
              Done
              {count(done.length)}
            </TabsTrigger>
            <TabsTrigger value="dismissed">
              Dismissed
              {count(dismissed.length)}
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-5 py-3">
          <div className="w-full sm:w-[22rem]">
            <Input placeholder="Search by title, guest, phone, property…" value={search} onChange={(e) => setSearch(e.target.value)} leadingIcon={<Search />} />
          </div>
          <FilterChip active={activeChip === "all"} dot="bg-foreground" onClick={() => setChip("all")}>
            All
          </FilterChip>
          {chips.kinds.map((kind) => (
            <FilterChip key={kind} active={activeChip === kind} dot={toneDotClassName[KIND_TONE[kind]]} onClick={() => setChip(kind)} className="capitalize">
              {KIND_LABEL[kind]}
            </FilterChip>
          ))}
          {chips.upcoming && (
            <FilterChip active={activeChip === "upcoming"} dot={toneDotClassName.live} onClick={() => setChip("upcoming")}>
              {PHASE_LABEL.upcoming}
            </FilterChip>
          )}
          <span className="ml-auto flex items-center gap-1.5 text-sm text-muted-foreground">
            <ArrowDownUp className="size-4" />
            {tab === "open" ? "Sorted by oldest open" : "Sorted by latest closed"}
          </span>
        </div>

        {!loaded ? (
          <div className="p-5">
            <Skeleton className="h-48 w-full" />
          </div>
        ) : inTab.length === 0 ? (
          <p className="p-5 text-sm text-muted-foreground">
            {tab === "open" ? "Nothing waiting on you." : tab === "done" ? "Nothing marked done yet." : "Nothing dismissed."}
          </p>
        ) : visible.length === 0 ? (
          <p className="p-5 text-sm text-muted-foreground">No tickets match.</p>
        ) : (
          <Table className="h-auto">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-4">Ticket</TableHead>
                <TableHead className="w-[16%]">Guest</TableHead>
                <TableHead className="w-[19%]">Property</TableHead>
                <TableHead className="w-[11%]">Age · SLA</TableHead>
                <TableHead className="w-[9%]">Created</TableHead>
                <TableHead className="w-[12%] pr-5 text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((ticket) => (
                <TicketRow key={ticket.id} ticket={ticket} now={now} onOpen={(t) => setOpenId(t.id)} onChanged={changed} />
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <TicketDetailPanel ticket={opened} onOpenChange={(isOpen) => !isOpen && setOpenId(null)} onChanged={changed} />
    </div>
  );
}
