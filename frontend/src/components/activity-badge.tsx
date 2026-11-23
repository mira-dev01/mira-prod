import { cn } from "@/lib/utils";

/**
 * A solid red circle with a number: how many things need the host (requests from guests with a booking they have not
 * seen, open tickets). Brick is the dashboard's colour for "needs attention" counts (DESIGN.md). Nothing is drawn for
 * zero. Over 9 shows "9+" so the circle keeps its shape. `noun` is what is counted, for screen readers.
 */
export function ActivityBadge({ count, className, noun = "request" }: { count: number; className?: string; noun?: string }) {
  if (count <= 0) return null;
  return (
    <span
      role="status"
      aria-label={`${count} ${noun}${count === 1 ? "" : "s"} needing attention`}
      className={cn(
        "inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-primary text-[11px] leading-none font-bold text-primary-foreground tabular-nums",
        className
      )}
    >
      {count > 9 ? "9+" : count}
    </span>
  );
}
