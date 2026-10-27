"use client";

import { useState } from "react";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { BadgeCheck, CheckCircle2, Flame, IndianRupee } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RightPanelFooterButton } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { BookingReconciliationPanel } from "@/components/booking-reconciliation";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/format";
import { cn, glassCardClassName } from "@/lib/utils";
import type { BookingReconciliation, OverviewData } from "@/lib/types";

type ReviewKind = "price" | "attribution";

function matches(kind: ReviewKind, item: BookingReconciliation): boolean {
  return kind === "price" ? item.booking.needs_price_confirmation : item.booking.needs_attribution_review;
}

function AttentionRow({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action: React.ReactNode;
}) {
  return (
    <li className="flex flex-wrap items-center gap-3 rounded-xl px-3 py-3 transition-colors hover:bg-accent/50 sm:flex-nowrap">
      <span
        className="flex size-9 shrink-0 items-center justify-center rounded-full"
        style={{ backgroundColor: "color-mix(in oklch, var(--status-pending) 16%, transparent)" }}
      >
        <Icon className="size-4" style={{ color: "var(--status-pending)" }} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <div className="ml-12 shrink-0 sm:ml-0">{action}</div>
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
    return <Skeleton className="h-16 w-full rounded-2xl" aria-label="Loading actions" />;
  }

  const { price_confirmations, attribution_confirmations, high_intent_unconverted: hot } = data.attention;
  const total = price_confirmations + attribution_confirmations + hot.count;
  const valueNote =
    hot.potential_value === null
      ? "Follow up while they're still deciding."
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
        <div
          className={cn(
            "flex items-center gap-3 rounded-2xl px-5 py-3.5 text-sm text-muted-foreground",
            glassCardClassName
          )}
        >
          <CheckCircle2 className="size-4 shrink-0" style={{ color: "var(--status-live)" }} />
          <span>
            <span className="font-medium text-foreground">Needs your attention</span> · No actions needed —
            you&apos;re all caught up.
          </span>
        </div>
        {reviewPanel}
      </>
    );
  }

  return (
    <Card className={glassCardClassName}>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Needs your attention</CardTitle>
        <Badge variant="outline">{total} open</Badge>
      </CardHeader>
      <CardContent>
        <ul className="space-y-1">
          {price_confirmations > 0 && (
            <AttentionRow
              icon={IndianRupee}
              title={`${price_confirmations} booking${price_confirmations === 1 ? " needs" : "s need"} price confirmation`}
              description="Confirm final booking prices to keep revenue accurate."
              action={
                <Button size="sm" variant="outline" disabled={opening !== null} onClick={() => startReview("price")}>
                  {opening === "price" ? "Opening…" : "Review"}
                </Button>
              }
            />
          )}
          {attribution_confirmations > 0 && (
            <AttentionRow
              icon={BadgeCheck}
              title={`${attribution_confirmations} potential Mira booking${attribution_confirmations === 1 ? " needs" : "s need"} confirmation`}
              description="Tell us whether Mira contributed to these bookings."
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
              icon={Flame}
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
    </Card>
  );
}
