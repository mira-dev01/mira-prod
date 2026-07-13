import { Badge } from "@/components/ui/badge";
import { toneBadgeVariant, toneClassName, toneDotClassName, type StatusTone } from "@/lib/tone";
import { cn } from "@/lib/utils";

export type { StatusTone };

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
