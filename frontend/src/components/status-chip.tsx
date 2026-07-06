import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type StatusTone = "live" | "pending" | "progress" | "destructive" | "neutral";

const toneClassName: Record<StatusTone, string> = {
  live: "badge-status-live",
  pending: "badge-status-pending",
  progress: "badge-status-progress",
  destructive: "",
  neutral: "",
};

const toneDotClassName: Record<StatusTone, string> = {
  live: "bg-(--status-live)",
  pending: "bg-(--status-pending)",
  progress: "bg-(--status-progress)",
  destructive: "bg-destructive",
  neutral: "bg-muted-foreground",
};

const toneBadgeVariant: Record<StatusTone, "outline" | "destructive"> = {
  live: "outline",
  pending: "outline",
  progress: "outline",
  destructive: "destructive",
  neutral: "outline",
};

type StatusChipProps = {
  status: string;
  tone: StatusTone;
  className?: string;
};

export function StatusChip({ status, tone, className }: StatusChipProps) {
  return (
    <Badge variant={toneBadgeVariant[tone]} className={cn(toneClassName[tone], "capitalize", className)}>
      <span className={cn("size-1.5 rounded-full", toneDotClassName[tone])} />
      {status}
    </Badge>
  );
}
