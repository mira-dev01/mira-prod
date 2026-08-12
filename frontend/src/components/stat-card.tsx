import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, glassCardClassName } from "@/lib/utils";

type StatCardProps = {
  icon: LucideIcon;
  /** Only pass a status/semantic color var (e.g. --destructive) when this
   * stat IS a status signal. Everything else should use the shared
   * neutral default so icon color doesn't compete with real urgency color-
   * coding elsewhere in the dashboard. */
  iconColorVar?: string;
  label: string;
  value?: number | string;
  loading?: boolean;
  /** Adds the shared hover-lift treatment -- only pass this when the card is
   * itself wrapped in a real interactive element (e.g. a Link), so the
   * lift affordance isn't shown on purely informational stat cards. */
  interactive?: boolean;
  /** Frosted-glass treatment for use over a gradient backdrop -- opt-in only,
   * for zones that actually have something behind them worth blurring.
   * Default stays fully opaque for every other consumer (landing-page
   * mockup, recovery-analytics-card) sitting on a flat surface. */
  glass?: boolean;
  className?: string;
};

export function StatCard({
  icon: Icon,
  iconColorVar = "--accent-warm",
  label,
  value,
  loading,
  interactive,
  glass,
  className,
}: StatCardProps) {
  return (
    <Card
      className={cn(
        interactive && "surface-interactive",
        glass && glassCardClassName,
        className
      )}
    >
      <CardContent className="space-y-2">
        <span
          className="flex size-8 items-center justify-center rounded-full"
          style={{ backgroundColor: `color-mix(in oklch, var(${iconColorVar}) 16%, transparent)` }}
        >
          <Icon className="size-4" style={{ color: `var(${iconColorVar})` }} />
        </span>
        <p className="text-sm text-muted-foreground">{label}</p>
        {loading ? <Skeleton className="h-7 w-16" /> : <p className="text-2xl font-semibold">{value ?? "—"}</p>}
      </CardContent>
    </Card>
  );
}
