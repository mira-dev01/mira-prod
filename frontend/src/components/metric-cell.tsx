import { Cell, CellTitle, Hint } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * One headline figure as a ruled block: a small caps label, a large number in
 * the display face, and an optional one-line note. Two across on phones, four
 * across on wide screens. `accent` makes the number brick: use it for the one
 * hero figure of a view, never for several.
 */
export function MetricCell({
  label,
  value,
  note,
  accent,
  muted,
  loading,
  className,
}: {
  label: string;
  value: string | number;
  note?: React.ReactNode;
  accent?: boolean;
  /** The value is a placeholder ("—"), so show it quieter. */
  muted?: boolean;
  loading?: boolean;
  className?: string;
}) {
  return (
    <Cell className={cn("col-span-6 p-4 sm:p-5 xl:col-span-3", className)}>
      <CellTitle>{label}</CellTitle>
      {loading ? (
        <Skeleton className="h-9 w-20" />
      ) : (
        <p
          className={cn(
            "font-heading text-[clamp(1.75rem,2.4vw,2.25rem)] leading-tight font-semibold tracking-tight tabular-nums",
            accent && !muted && "text-primary",
            muted && "text-muted-foreground"
          )}
        >
          {value}
        </p>
      )}
      {note && !loading && <Hint>{note}</Hint>}
    </Cell>
  );
}
