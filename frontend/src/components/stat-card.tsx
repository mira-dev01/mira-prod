import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Sparkline } from "@/components/sparkline";
import { cn } from "@/lib/utils";

export type StatCardTrend = {
  value: number;
  direction: "up" | "down";
};

type StatCardProps = {
  icon: LucideIcon;
  iconColorVar: string;
  label: string;
  value?: number | string;
  trend?: StatCardTrend;
  sparklineData?: number[];
  loading?: boolean;
};

export function StatCard({ icon: Icon, iconColorVar, label, value, sparklineData, loading }: StatCardProps) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-2">
          <span
            className={cn("flex size-8 items-center justify-center rounded-full")}
            style={{ backgroundColor: `color-mix(in oklch, var(${iconColorVar}) 16%, transparent)` }}
          >
            <Icon className="size-4" style={{ color: `var(${iconColorVar})` }} />
          </span>
          <p className="text-sm text-muted-foreground">{label}</p>
          {loading ? (
            <Skeleton className="h-7 w-16" />
          ) : (
            <p className="text-2xl font-semibold">{value ?? "—"}</p>
          )}
        </div>
        {sparklineData && sparklineData.length > 0 && !loading && (
          <Sparkline data={sparklineData} colorVar={iconColorVar} />
        )}
      </CardContent>
    </Card>
  );
}
