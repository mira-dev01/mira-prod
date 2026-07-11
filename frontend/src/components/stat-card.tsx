import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

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
};

export function StatCard({ icon: Icon, iconColorVar = "--accent-warm", label, value, loading }: StatCardProps) {
  return (
    <Card>
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
