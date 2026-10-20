"use client";

import type { LucideIcon } from "lucide-react";
import { Info } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { Decision, FunnelStep, HistogramBucket } from "@/lib/admin-api";

// ── formatters ──────────────────────────────────────────────────────────────

export function fmtPct(value: number | null | undefined, digits = 0): string {
  return value == null ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function fmtNum(value: number | null | undefined, digits = 0): string {
  if (value == null) return "—";
  return value.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function fmtSec(value: number | null | undefined): string {
  if (value == null) return "—";
  return value < 60 ? `${value.toFixed(value < 10 ? 2 : 1)}s` : `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

export function fmtMoney(value: number | null | undefined, currency = "INR"): string {
  if (value == null) return "—";
  const symbol = currency === "USD" ? "$" : "₹";
  const digits = Math.abs(value) < 10 ? 2 : 0;
  return `${symbol}${value.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits })}`;
}

export function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── layout ──────────────────────────────────────────────────────────────────

export function PageHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div>
      <h1 className="page-title">{title}</h1>
      <p className="text-sm text-muted-foreground">{subtitle}</p>
    </div>
  );
}

export function Section({
  title,
  description,
  children,
  className,
  action,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
  action?: React.ReactNode;
}) {
  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{title}</CardTitle>
            {description && <CardDescription>{description}</CardDescription>}
          </div>
          {action}
        </div>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

/** Compact label/value pair used inside Sections. */
export function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: "good" | "warn" | "bad";
}) {
  const color =
    tone === "good" ? "var(--status-live)" : tone === "warn" ? "var(--status-pending)" : tone === "bad" ? "var(--destructive)" : undefined;
  return (
    <div className="space-y-0.5">
      <p className="text-micro text-muted-foreground">{label}</p>
      <p className="text-xl font-semibold tabular-nums" style={color ? { color } : undefined}>
        {value}
      </p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function StatGrid({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-4", className)}>{children}</div>;
}

export function CoverageNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
      <Info className="mt-0.5 size-3.5 shrink-0" />
      <span>{children}</span>
    </div>
  );
}

export function LoadingGrid({ cards = 4 }: { cards?: number }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {Array.from({ length: cards }).map((_, i) => (
        <Skeleton key={i} className="h-48 w-full rounded-xl" />
      ))}
    </div>
  );
}

export function ErrorState({ error }: { error: Error }) {
  return (
    <Card>
      <CardContent className="py-6 text-sm text-destructive">Couldn&apos;t load this section: {error.message}</CardContent>
    </Card>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-4 text-center text-sm text-muted-foreground">{children}</p>;
}

// ── charts (plain DOM/SVG, theme-token colored) ─────────────────────────────

export function BarList({
  items,
  colorVar = "--primary",
  format = (v: number) => fmtNum(v),
  emptyLabel = "No data in this range",
}: {
  items: { label: string; value: number; hint?: string }[];
  colorVar?: string;
  format?: (v: number) => string;
  emptyLabel?: string;
}) {
  const max = Math.max(0, ...items.map((i) => i.value));
  if (items.length === 0 || max === 0) return <Empty>{emptyLabel}</Empty>;
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.label} className="space-y-1">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="truncate">{item.label}</span>
            <span className="shrink-0 tabular-nums text-muted-foreground">
              {format(item.value)}
              {item.hint && <span className="ml-1.5 text-xs">{item.hint}</span>}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-muted">
            <div
              className="h-1.5 rounded-full transition-[width] duration-500"
              style={{ width: `${(item.value / max) * 100}%`, backgroundColor: `var(${colorVar})` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function Histogram({
  buckets,
  label = (b: HistogramBucket) => `${b.from}`,
  colorVar = "--status-progress",
  highlight,
}: {
  buckets: HistogramBucket[];
  label?: (b: HistogramBucket) => string;
  colorVar?: string;
  /** Buckets to draw in the warning color (e.g. below a threshold). */
  highlight?: (b: HistogramBucket) => boolean;
}) {
  const max = Math.max(0, ...buckets.map((b) => b.count));
  if (max === 0) return <Empty>No samples yet</Empty>;
  return (
    <div>
      <div className="flex h-32 items-end gap-1">
        {buckets.map((b) => (
          <div key={`${b.from}-${b.to}`} className="group relative flex h-full flex-1 flex-col justify-end">
            <div
              className="rounded-t-sm transition-[height] duration-500"
              style={{
                height: `${Math.max(2, (b.count / max) * 100)}%`,
                backgroundColor: `var(${highlight?.(b) ? "--status-pending" : colorVar})`,
                opacity: b.count === 0 ? 0.25 : 1,
              }}
            />
            <span className="pointer-events-none absolute -top-5 left-1/2 -translate-x-1/2 rounded bg-foreground px-1 text-[10px] text-background opacity-0 group-hover:opacity-100">
              {b.count}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-1">
        {buckets.map((b) => (
          <span key={`${b.from}-${b.to}-l`} className="flex-1 truncate text-center text-[10px] text-muted-foreground">
            {label(b)}
          </span>
        ))}
      </div>
    </div>
  );
}

export function Funnel({ steps }: { steps: FunnelStep[] }) {
  const max = Math.max(1, ...steps.map((s) => s.count));
  return (
    <ol className="space-y-2.5">
      {steps.map((step) => (
        <li key={step.key} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 text-sm">
          <span className="truncate">
            {step.label}
            {step.telemetry_only && <span className="ml-1 text-xs text-muted-foreground">*</span>}
          </span>
          <div className="h-6 rounded-md bg-muted">
            <div
              className="flex h-6 items-center rounded-md px-2 text-xs font-medium text-primary-foreground transition-[width] duration-500"
              style={{
                width: `${Math.max(step.count ? 8 : 0, (step.count / max) * 100)}%`,
                backgroundColor: step.telemetry_only
                  ? "color-mix(in oklch, var(--primary) 60%, var(--card))"
                  : "var(--primary)",
              }}
            >
              {step.count > 0 && fmtNum(step.count)}
            </div>
          </div>
          <span className="w-14 text-right text-xs tabular-nums text-muted-foreground">
            {step.rate_of_previous != null ? fmtPct(step.rate_of_previous) : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

const VERDICT_STYLE: Record<Decision["verdict"], { label: string; color: string; bg: string }> = {
  enable: { label: "Enable", color: "var(--status-live)", bg: "var(--status-live-bg)" },
  hold: { label: "Hold", color: "var(--status-progress)", bg: "var(--status-progress-bg)" },
  insufficient_data: { label: "Need more calls", color: "var(--priority-low)", bg: "var(--priority-low-bg)" },
};

export function VerdictPill({ verdict }: { verdict: Decision["verdict"] }) {
  const s = VERDICT_STYLE[verdict];
  return (
    <span className="inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium" style={{ color: s.color, backgroundColor: s.bg }}>
      {s.label}
    </span>
  );
}

export function DecisionList({ decisions }: { decisions: Decision[] }) {
  return (
    <ul className="divide-y">
      {decisions.map((d) => (
        <li key={d.key} className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
          <div className="min-w-0 space-y-0.5">
            <p className="text-sm font-medium">
              <span className="mr-1.5 text-muted-foreground">{d.key}</span>
              {d.label}
            </p>
            <p className="text-xs text-muted-foreground">
              {d.metric}: <span className="tabular-nums text-foreground">{d.value == null ? "—" : d.value < 1 && d.value > 0 ? fmtPct(d.value, 1) : fmtNum(d.value, 2)}</span>
              {" · "}
              {d.rule}
            </p>
          </div>
          <VerdictPill verdict={d.verdict} />
        </li>
      ))}
    </ul>
  );
}

const LEVEL_STYLE: Record<string, { color: string; bg: string; label: string }> = {
  ok: { color: "var(--status-live)", bg: "var(--status-live-bg)", label: "Healthy" },
  low: { color: "var(--status-pending)", bg: "var(--status-pending-bg)", label: "Low" },
  critical: { color: "var(--destructive)", bg: "color-mix(in oklch, var(--destructive) 12%, transparent)", label: "Recharge" },
  unknown: { color: "var(--priority-low)", bg: "var(--priority-low-bg)", label: "—" },
  not_set: { color: "var(--priority-low)", bg: "var(--priority-low-bg)", label: "Not set" },
  not_configured: { color: "var(--priority-low)", bg: "var(--priority-low-bg)", label: "Not configured" },
  error: { color: "var(--destructive)", bg: "color-mix(in oklch, var(--destructive) 12%, transparent)", label: "Error" },
};

export function LevelPill({ level }: { level: string }) {
  const s = LEVEL_STYLE[level] ?? LEVEL_STYLE.unknown;
  return (
    <span className="inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium" style={{ color: s.color, backgroundColor: s.bg }}>
      {s.label}
    </span>
  );
}

export function IconBadge({ icon: Icon, colorVar = "--accent-warm" }: { icon: LucideIcon; colorVar?: string }) {
  return (
    <span
      className="flex size-8 items-center justify-center rounded-full"
      style={{ backgroundColor: `color-mix(in oklch, var(${colorVar}) 16%, transparent)` }}
    >
      <Icon className="size-4" style={{ color: `var(${colorVar})` }} />
    </span>
  );
}
