import type { LucideIcon } from "lucide-react";
import { TrendingDown, TrendingUp } from "lucide-react";
import { InfoTip } from "@/components/ui/info-tip";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * One headline number for the Analytics page. Same visual language as
 * StatCard (icon chip, muted label, semibold value) but borderless so a
 * section card can hold several without nesting boxes inside boxes.
 */
export function MetricTile({
  icon: Icon,
  label,
  value,
  hint,
  change,
  changeLabel,
  loading,
  muted,
  info,
  footer,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  /** One short line under the value (a definition, sample size, caveat). */
  hint?: React.ReactNode;
  /** Relative change vs. the comparison period (0.12 = +12%). */
  change?: number | null;
  changeLabel?: string;
  loading?: boolean;
  /** Value is a placeholder ("—" / "Not enough data yet"), render it quieter. */
  muted?: boolean;
  /** What the metric means and how it's calculated, behind an ⓘ. */
  info?: React.ReactNode;
  /** A note or action under the tile (e.g. "Confirm 3 booking prices"). */
  footer?: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5 rounded-lg p-3">
      <div className="flex items-center gap-2">
        <span
          className="flex size-7 items-center justify-center rounded-full"
          style={{ backgroundColor: "color-mix(in oklch, var(--accent-warm) 16%, transparent)" }}
        >
          <Icon className="size-3.5" style={{ color: "var(--accent-warm)" }} />
        </span>
        <p className="text-sm text-muted-foreground">{label}</p>
        {info && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
      </div>
      {loading ? (
        <Skeleton className="h-7 w-20" />
      ) : (
        <p className={cn("text-2xl font-semibold tabular-nums", muted && "text-base font-medium text-muted-foreground")}>
          {value}
        </p>
      )}
      {!loading && change !== undefined && change !== null && (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          {change >= 0 ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}
          {change >= 0 ? "+" : ""}
          {(change * 100).toFixed(0)}% {changeLabel}
        </p>
      )}
      {!loading && hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {!loading && footer}
    </div>
  );
}

/**
 * Horizontal single-series bar list (funnel stages, guest intent). One hue,
 * thin bars with rounded data ends, values in text ink beside each bar so
 * nothing is encoded by color alone; each row carries a hover title and an
 * aria-label with the exact numbers.
 */
export function BarList({
  rows,
}: {
  rows: { key: string; label: string; value: number; detail?: string; sublabel?: string }[];
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="space-y-3">
      {rows.map((row) => {
        const width = row.value === 0 ? 0 : Math.max(2, (row.value / max) * 100);
        const description = `${row.label}: ${row.value.toLocaleString("en-IN")}${row.detail ? ` (${row.detail})` : ""}`;
        return (
          <li key={row.key} className="space-y-1" title={description} aria-label={description}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span>
                {row.label}
                {row.sublabel && <span className="ml-1.5 text-xs text-muted-foreground">{row.sublabel}</span>}
              </span>
              <span className="shrink-0 tabular-nums">
                <span className="font-medium">{row.value.toLocaleString("en-IN")}</span>
                {row.detail && <span className="ml-1.5 text-xs text-muted-foreground">{row.detail}</span>}
              </span>
            </div>
            <div className="h-2 w-full rounded-full bg-muted">
              <div className="h-2 rounded-full bg-(--chart-1) transition-[width]" style={{ width: `${width}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
