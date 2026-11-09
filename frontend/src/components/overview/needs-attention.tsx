"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CardContent, CardHeader } from "@/components/ui/card";
import { Cell, CellCard, CellTitle } from "@/components/ui/sheet";
import { RightPanelFooterButton } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { BookingReconciliationPanel } from "@/components/booking-reconciliation";
import { api } from "@/lib/api";
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
 * Overview's "Needs your attention" -- current-state actions, highest
 * priority first: booking prices to confirm, possible Mira bookings to
 * confirm, high-intent guests who haven't converted. "Review" opens the
 * same reconciliation panel as the Calendar/Analytics page right here and
 * steps through the queue, so the host acts without leaving Overview.
 */
export function NeedsAttentionCard({
  data,
  loading,
  onChanged,
}: {
  data: OverviewData | null;
  loading: boolean;
  onChanged: () => void;
}) {
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
      <Cell>
        <Skeleton className="h-5 w-40" aria-label="Loading actions" />
      </Cell>
    );
  }

  const { price_confirmations, attribution_confirmations, high_intent_unconverted: hot } = data.attention;
  const total = price_confirmations + attribution_confirmations + hot.count;
  const valueNote =
    hot.potential_value === null
      ? undefined
      : hot.valued_count === hot.count
        ? `${formatINR(hot.potential_value)} potential booking value`
        : `${formatINR(hot.potential_value)} quoted to ${hot.valued_count} of them`;

  // Nothing to do: a single quiet line, not a full card competing with the
  // snapshot above it.
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
        <Cell className="flex-row items-center gap-3 py-3 text-sm">
          <span aria-hidden className="size-2 shrink-0 rounded-full bg-status-live" />
          <span>
            <span className="font-semibold">Needs your attention</span>
            <span className="text-muted-foreground"> · Nothing right now.</span>
          </span>
        </Cell>
        {reviewPanel}
      </>
    );
  }

  return (
    <CellCard>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CellTitle>Needs your attention</CellTitle>
        <Badge variant="outline">{total} open</Badge>
      </CardHeader>
      <CardContent>
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
                <Button size="sm" variant="outline" render={<Link href="/dashboard/leads?tab=booking&status=open" />}>
                  View guests
                </Button>
              }
            />
          )}
        </ul>
      </CardContent>
      {reviewPanel}
    </CellCard>
  );
}
