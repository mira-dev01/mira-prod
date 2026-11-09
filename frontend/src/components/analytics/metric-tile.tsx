import { TrendingDown, TrendingUp } from "lucide-react";
import { InfoTip } from "@/components/ui/info-tip";
import { CellTitle, Hint } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * One headline number for the Analytics page: the content of a ruled Cell (a
 * small caps label, the number in the display face, change and hint lines).
 * `accent` makes the number brick, for the page's one hero figure only.
 */
export function MetricTile({
  label,
  value,
  hint,
  change,
  changeLabel,
  loading,
  muted,
  accent,
  info,
  footer,
}: {
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
  accent?: boolean;
  /** What the metric means and how it's calculated, behind an ⓘ. */
  info?: React.ReactNode;
  /** A note or action under the tile (e.g. "Confirm 3 booking prices"). */
  footer?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <CellTitle>{label}</CellTitle>
        {info && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
      </div>
      {loading ? (
        <Skeleton className="h-9 w-20" />
      ) : (
        <p
          className={cn(
            "font-heading text-[clamp(1.75rem,2.4vw,2.25rem)] leading-tight font-semibold tracking-tight tabular-nums",
            accent && !muted && "text-primary",
            muted && "text-base font-medium text-muted-foreground"
          )}
        >
          {value}
        </p>
      )}
      {!loading && change !== undefined && change !== null && (
        <p className="flex items-center gap-1 text-[13px] text-muted-foreground">
          {change >= 0 ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}
          {change >= 0 ? "+" : ""}
          {(change * 100).toFixed(0)}% {changeLabel}
        </p>
      )}
      {!loading && hint && <Hint>{hint}</Hint>}
      {!loading && footer}
    </div>
  );
}

/**
 * Horizontal single-series bar list (funnel stages, guest intent). One hue,
 * thin square bars, values in text ink beside each bar so
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
                {row.sublabel && <span className="ml-1.5 text-[13px] text-muted-foreground">{row.sublabel}</span>}
              </span>
              <span className="shrink-0 tabular-nums">
                <span className="font-semibold">{row.value.toLocaleString("en-IN")}</span>
                {row.detail && <span className="ml-1.5 text-[13px] text-muted-foreground">{row.detail}</span>}
              </span>
            </div>
            <div className="h-2 w-full bg-muted">
              <div className="h-2 bg-(--status-pending-strong) transition-[width]" style={{ width: `${width}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
