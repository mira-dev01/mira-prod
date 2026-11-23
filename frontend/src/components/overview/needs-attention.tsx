"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, CircleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { CardContent, CardHeader } from "@/components/ui/card";
import { Cell, CellCard, CellTitle } from "@/components/ui/sheet";
import { RightPanelFooterButton } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { BookingReconciliationPanel } from "@/components/booking-reconciliation";
import { useAsync } from "@/hooks/use-async";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { InfoButton } from "@/components/overview/info-card";
import { KIND_LABEL } from "@/components/ticket-card";
import { formatINR } from "@/lib/format";
import type { BookingReconciliation, OverviewData } from "@/lib/types";

type ReviewKind = "price" | "attribution";

function matches(kind: ReviewKind, item: BookingReconciliation): boolean {
  return kind === "price" ? item.booking.needs_price_confirmation : item.booking.needs_attribution_review;
}

function AttentionRow({ title, description, action }: { title: string; description?: string; action: React.ReactNode }) {
  return (
    <li className="flex flex-wrap items-center gap-3 border-b border-border py-3 first:pt-0 last:border-0 last:pb-0 sm:flex-nowrap">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        {description && <p className="text-[13px] text-muted-foreground">{description}</p>}
      </div>
      <div className="shrink-0">{action}</div>
    </li>
  );
}

/**
 * Overview's "Tickets raised" -- current-state actions, highest
 * priority first: booking prices to confirm, possible Mira bookings to
 * confirm, high-intent guests who haven't converted, then the open tickets
 * themselves. A red count next to the title says how many are waiting. "Review" opens the
 * same reconciliation panel as the Calendar/Analytics page right here and
 * steps through the queue, so the host acts without leaving Overview.
 */
/** Rows shown on the card; the rest are one click away on the Tickets tab. */
const MAX_ROWS = 5;

/** The round red count beside the title: how many things are waiting on the host. */
function CountBubble({ count }: { count: number }) {
  return (
    <span
      aria-label={`${count} pending`}
      className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-destructive px-1.5 text-[11px] leading-none font-bold text-white tabular-nums"
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

export function NeedsAttentionCard({
  data,
  loading,
  onChanged,
  className,
}: {
  data: OverviewData | null;
  loading: boolean;
  onChanged: () => void;
  /** Grid placement inside the Overview's Sheet. */
  className?: string;
}) {
  // Open tickets are current state, like the rest of this card.
  const { data: tickets } = useAsync(() => api.tickets.list("open"), []);
  const [reviewKind, setReviewKind] = useState<ReviewKind | null>(null);
  const [queue, setQueue] = useState<BookingReconciliation[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [opening, setOpening] = useState<ReviewKind | null>(null);

  async function startReview(kind: ReviewKind) {
    setOpening(kind);
    try {
      // Server-side kind filter: the queue is paged, so filtering a mixed
      // page client-side could miss items the card counted.
      const result = await api.bookings.reconciliationQueue(kind);
      const items = result.items.filter((item) => matches(kind, item));
      if (items.length === 0) {
        toast.success("Nothing left to review");
        onChanged();
        return;
      }
      setQueue(items);
      setReviewKind(kind);
      setCurrentId(items[0].booking.id);
    } catch {
      toast.error("Couldn't load bookings to review");
    } finally {
      setOpening(null);
    }
  }

  const position = currentId ? queue.findIndex((item) => item.booking.id === currentId) : -1;
  const nextItem = position >= 0 ? queue[position + 1] : undefined;

  function closeReview() {
    setCurrentId(null);
    setReviewKind(null);
    setQueue([]);
  }

  if (loading || !data) {
    return (
      <Cell className={className}>
        <Skeleton className="h-5 w-40" aria-label="Loading actions" />
      </Cell>
    );
  }

  const { price_confirmations, attribution_confirmations, high_intent_unconverted: hot } = data.attention;
  const openTickets = tickets ?? [];
  const total = price_confirmations + attribution_confirmations + hot.count + openTickets.length;
  const summaryRows = (price_confirmations > 0 ? 1 : 0) + (attribution_confirmations > 0 ? 1 : 0) + (hot.count > 0 ? 1 : 0);
  const ticketRows = openTickets.slice(0, Math.max(0, MAX_ROWS - summaryRows));
  const valueNote =
    hot.potential_value === null
      ? undefined
      : hot.valued_count === hot.count
        ? `${formatINR(hot.potential_value)} potential booking value`
        : `${formatINR(hot.potential_value)} quoted to ${hot.valued_count} of them`;

  const reviewPanel = (
    <BookingReconciliationPanel
      bookingId={currentId}
      onOpenChange={(open) => !open && closeReview()}
      onChanged={onChanged}
      footer={
        reviewKind && queue.length > 1 ? (
          <RightPanelFooterButton
            variant="outline"
            onClick={() => (nextItem ? setCurrentId(nextItem.booking.id) : closeReview())}
          >
            {nextItem ? `Next (${queue.length - position - 1} left)` : "Done"}
          </RightPanelFooterButton>
        ) : undefined
      }
    />
  );

  if (total === 0) {
    return (
      <>
        <Cell className={cn("gap-4 p-5", className)}>
          <CellTitle icon={CircleAlert}>
            <Link href="/dashboard/leads?tab=tickets" className="hover:underline">
              Tickets raised
            </Link>
            <InfoButton info={data.info?.needs_attention} />
          </CellTitle>
          <p className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
            <span aria-hidden className="size-2 shrink-0 rounded-full bg-status-live" />
            Nothing waiting on you right now.
          </p>
        </Cell>
        {reviewPanel}
      </>
    );
  }

  return (
    <CellCard className={className}>
      <CardHeader>
        <CellTitle icon={CircleAlert}>
          <Link href="/dashboard/leads?tab=tickets" className="hover:underline">
            Tickets raised
          </Link>
          <CountBubble count={total} />
          <InfoButton info={data.info?.needs_attention} />
        </CellTitle>
      </CardHeader>
      <CardContent className="flex-1">
        <ul>
          {price_confirmations > 0 && (
            <AttentionRow
              title={`${price_confirmations} booking${price_confirmations === 1 ? " needs" : "s need"} price confirmation`}
              action={
                <Button size="sm" variant="outline" disabled={opening !== null} onClick={() => startReview("price")}>
                  {opening === "price" ? "Opening…" : "Review"}
                </Button>
              }
            />
          )}
          {attribution_confirmations > 0 && (
            <AttentionRow
              title={`${attribution_confirmations} potential Mira booking${attribution_confirmations === 1 ? " needs" : "s need"} confirmation`}
              action={
                <Button
                  size="sm"
                  variant="outline"
                  disabled={opening !== null}
                  onClick={() => startReview("attribution")}
                >
                  {opening === "attribution" ? "Opening…" : "Review"}
                </Button>
              }
            />
          )}
          {hot.count > 0 && (
            <AttentionRow
              title={`${hot.count} high-intent guest${hot.count === 1 ? " hasn't" : "s haven't"} converted`}
              description={valueNote}
              action={
                <Button size="sm" variant="outline" nativeButton={false} render={<Link href="/dashboard/leads?tab=leads&status=open" />}>
                  View guests
                </Button>
              }
            />
          )}
          {ticketRows.map((t) => (
            <AttentionRow
              key={t.id}
              title={t.summary}
              description={`${KIND_LABEL[t.kind] ?? t.kind}${t.guest_name ? ` · ${t.guest_name}` : ""}`}
              action={
                <Button size="sm" variant="outline" nativeButton={false} render={<Link href="/dashboard/leads?tab=tickets" />}>
                  Open
                </Button>
              }
            />
          ))}
        </ul>
      </CardContent>
      <div className="border-t border-border px-5 py-3">
        <Link href="/dashboard/leads?tab=tickets" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          View all tickets <ArrowRight className="size-3.5" />
        </Link>
      </div>
      {reviewPanel}
    </CellCard>
  );
}
