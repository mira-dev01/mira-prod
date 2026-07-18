import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Shared row primitive for "list of items inside a card" patterns --
 * notifications, unanswered questions, actionable summaries. Two framing
 * variants cover every existing consumer:
 *  - "divider" (default): rows separated by a bottom border, last row flush
 *    (ActionableCard, UnansweredQuestionsCard).
 *  - "boxed": each row is its own bordered/rounded box, for content dense
 *    enough to want visual separation beyond a hairline (LiveRequestsCard).
 */
function ListRow({
  className,
  variant = "divider",
  interactive = false,
  muted = false,
  ...props
}: React.ComponentProps<"div"> & {
  variant?: "divider" | "boxed";
  interactive?: boolean;
  muted?: boolean;
}) {
  return (
    <div
      data-slot="list-row"
      data-variant={variant}
      className={cn(
        "flex w-full flex-col gap-2.5 text-left",
        variant === "divider" && "border-b pb-3 last:border-0 last:pb-0",
        variant === "boxed" && "rounded-lg border p-3",
        interactive && "surface-hover cursor-pointer rounded-md",
        muted && "opacity-60",
        className
      )}
      {...props}
    />
  );
}

/** Top line of a row: leading content (chip/title) on the left, a trailing
 * timestamp/metadata/action on the right. Wraps on narrow widths rather
 * than truncating, since urgency chips and names must stay fully legible. */
function ListRowHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="list-row-header"
      className={cn("flex flex-wrap items-start justify-between gap-2", className)}
      {...props}
    />
  );
}

function ListRowTitle({ className, ...props }: React.ComponentProps<"p">) {
  return <p data-slot="list-row-title" className={cn("min-w-0 truncate text-sm font-medium", className)} {...props} />;
}

function ListRowBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="list-row-body" className={cn("min-w-0 flex-1 text-sm text-muted-foreground", className)} {...props} />;
}

function ListRowFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="list-row-footer" className={cn("flex items-center gap-2", className)} {...props} />;
}

export { ListRow, ListRowHeader, ListRowTitle, ListRowBody, ListRowFooter };
