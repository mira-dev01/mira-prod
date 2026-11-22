"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import type { OverviewMetricInfo } from "@/lib/types";

/** What a card means, how it is calculated and where it comes from -- text served by
 * GET /analytics/overview (backend overview_metric_info.py), never written here. */
export function MetricInfoBody({ info }: { info: OverviewMetricInfo }) {
  return (
    <div className="space-y-2">
      <p className="font-heading text-sm font-semibold text-heading">{info.label}</p>
      <p>{info.definition}</p>
      <dl className="space-y-1.5 text-muted-foreground">
        <div>
          <dt className="font-semibold text-foreground">How it&apos;s calculated</dt>
          <dd>{info.formula}</dd>
        </div>
        <div>
          <dt className="font-semibold text-foreground">Source</dt>
          <dd>{info.source}</dd>
        </div>
        <div>
          <dt className="font-semibold text-foreground">Period</dt>
          <dd>{info.window === "period" ? "Follows the date range above." : "Current state; the date range doesn't change it."}</dd>
        </div>
      </dl>
    </div>
  );
}

/**
 * A white card whose hover (mouse), focus (keyboard) and ⓘ button (touch and keyboard)
 * all reveal the metric's info. With `href` the whole card is one link; without it the
 * card holds its own links (a table's rows, a title link) and only the info is shared.
 * `children` receives the ⓘ button so each card can place it beside its own title.
 */
export function InfoCard({
  info,
  href,
  label,
  className,
  openDelay = 350,
  children,
}: {
  info: OverviewMetricInfo | undefined;
  href?: string;
  /** Accessible name of the whole-card link. */
  label?: string;
  className?: string;
  openDelay?: number;
  children: (infoButton: React.ReactNode) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  function schedule(next: boolean) {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(next), next ? openDelay : 120);
  }

  const infoButton = info ? (
    <PopoverTrigger
      aria-label={`About ${info.label}`}
      className="relative z-10 inline-flex size-5 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      <Info className="size-3.5" />
    </PopoverTrigger>
  ) : null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <div
        className={cn("relative rounded-xl border border-border bg-card transition-colors hover:border-input", className)}
        onPointerEnter={(e) => info && e.pointerType === "mouse" && schedule(true)}
        onPointerLeave={(e) => info && e.pointerType === "mouse" && schedule(false)}
        onFocus={(e) => info && e.target.matches(":focus-visible") && schedule(true)}
        onBlur={(e) => info && !e.currentTarget.contains(e.relatedTarget as Node | null) && schedule(false)}
      >
        {children(infoButton)}
        {href && (
          <Link
            href={href}
            aria-label={label}
            className="absolute inset-0 rounded-xl focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
          />
        )}
        {info && (
          <PopoverContent side="top" className="w-72 gap-2 text-xs leading-relaxed">
            <MetricInfoBody info={info} />
          </PopoverContent>
        )}
      </div>
    </Popover>
  );
}

/** Just the ⓘ with the same hover/focus/tap info, for cards that already have their own layout and links. */
export function InfoButton({ info }: { info: OverviewMetricInfo | undefined }) {
  const [open, setOpen] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  if (!info) return null;
  const schedule = (next: boolean) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(next), next ? 250 : 120);
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={`About ${info.label}`}
        onPointerEnter={(e) => e.pointerType === "mouse" && schedule(true)}
        onPointerLeave={(e) => e.pointerType === "mouse" && schedule(false)}
        className="inline-flex size-5 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <Info className="size-3.5" />
      </PopoverTrigger>
      <PopoverContent side="top" className="w-72 gap-2 text-xs leading-relaxed">
        <MetricInfoBody info={info} />
      </PopoverContent>
    </Popover>
  );
}
