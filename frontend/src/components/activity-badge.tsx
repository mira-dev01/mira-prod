import { cn } from "@/lib/utils";

/**
 * A solid circle with a number: how many requests from guests with an active booking the host has not seen yet.
 * Brick is the dashboard's colour for "needs attention" counts (DESIGN.md). Nothing is drawn for zero. Over 9
 * shows "9+" so the circle keeps its shape.
 */
export function ActivityBadge({ count, className }: { count: number; className?: string }) {
  if (count <= 0) return null;
  return (
    <span
      role="status"
      aria-label={`${count} new request${count === 1 ? "" : "s"} from guests with a booking`}
      className={cn(
        "inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-primary text-[11px] leading-none font-bold text-primary-foreground tabular-nums",
        className
      )}
    >
      {count > 9 ? "9+" : count}
    </span>
  );
}
