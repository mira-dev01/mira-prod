import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ExpandableText } from "@/components/expandable-text";
import { ListRow } from "@/components/ui/list-row";
import { toneBadgeVariant, toneClassName, type StatusTone } from "@/lib/tone";
import { cn } from "@/lib/utils";

export type ActionableCardPriority = {
  label: string;
  tone: "high" | "medium" | "low";
};

// ActionableCard's priority vocabulary (high/medium/low) maps onto the
// shared StatusTone vocabulary so the badge styling comes from the same
// single source of truth as StatusChip and Calendar, rather than a second
// hand-rolled tone->className map.
const priorityStatusTone: Record<ActionableCardPriority["tone"], StatusTone> = {
  high: "destructive",
  medium: "pending",
  low: "low",
};

type ActionableCardProps = {
  title: string;
  summary?: string;
  metadata?: string;
  priority?: ActionableCardPriority;
  onClick?: () => void;
  /** Visually deprioritizes the row (e.g. a lead with no captured info yet). */
  muted?: boolean;
  /** Single-line row for fast triage -- no ExpandableText/"Read more". */
  compact?: boolean;
};

export function ActionableCard({ title, summary, metadata, priority, onClick, muted, compact }: ActionableCardProps) {
  // Deliberately NOT a native <button>: the summary renders ExpandableText's
  // "Read more" button, and a <button> nested inside a <button> is invalid
  // HTML (React hydration error). A div with role="button" + keyboard
  // handling is clickable and accessible while legally allowing the inner
  // control. ExpandableText's own button calls stopPropagation so it doesn't
  // also fire this card's onClick.
  const interactive = Boolean(onClick);

  const priorityTone = priority && priorityStatusTone[priority.tone];
  const priorityBadge = priority && priorityTone && (
    <Badge variant={toneBadgeVariant[priorityTone]} className={cn(toneClassName[priorityTone], "shrink-0")}>
      {priority.label}
    </Badge>
  );

  const keyDown = interactive
    ? (e: React.KeyboardEvent) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick?.();
        }
      }
    : undefined;

  if (compact) {
    return (
      <ListRow
        role={interactive ? "button" : undefined}
        tabIndex={interactive ? 0 : undefined}
        onClick={onClick}
        onKeyDown={keyDown}
        muted={muted}
        className={cn("flex-row items-center gap-3 py-2.5", interactive && "cursor-pointer")}
      >
        {priorityBadge}
        <p className="min-w-0 flex-1 truncate text-sm">
          <span className="font-medium">{title}</span>
          {summary && <span className="text-muted-foreground"> — {summary}</span>}
        </p>
        {metadata && <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">{metadata}</span>}
        {onClick && <ChevronRight className="size-4 shrink-0 text-muted-foreground" />}
      </ListRow>
    );
  }

  return (
    <ListRow
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onClick={onClick}
      onKeyDown={keyDown}
      muted={muted}
      className={cn("flex-row items-start justify-between gap-3", interactive && "cursor-pointer")}
    >
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium">{title}</p>
          {priorityBadge}
        </div>
        {summary && (
          <p className="text-sm text-muted-foreground">
            <ExpandableText text={summary} maxLength={120} />
          </p>
        )}
        {metadata && <p className="text-xs text-muted-foreground">{metadata}</p>}
      </div>
      {onClick && <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
    </ListRow>
  );
}
