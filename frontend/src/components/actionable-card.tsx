import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ExpandableText } from "@/components/expandable-text";
import { cn } from "@/lib/utils";

export type ActionableCardPriority = {
  label: string;
  tone: "high" | "medium" | "low";
};

const priorityBadgeVariant: Record<ActionableCardPriority["tone"], "destructive" | "outline"> = {
  high: "destructive",
  medium: "outline",
  low: "outline",
};

const priorityClassName: Record<ActionableCardPriority["tone"], string> = {
  high: "",
  medium: "badge-status-pending",
  low: "",
};

type ActionableCardProps = {
  title: string;
  summary?: string;
  metadata?: string;
  priority?: ActionableCardPriority;
  onClick?: () => void;
};

export function ActionableCard({ title, summary, metadata, priority, onClick }: ActionableCardProps) {
  // Deliberately NOT a native <button>: the summary renders ExpandableText's
  // "Read more" button, and a <button> nested inside a <button> is invalid
  // HTML (React hydration error). A div with role="button" + keyboard
  // handling is clickable and accessible while legally allowing the inner
  // control. ExpandableText's own button calls stopPropagation so it doesn't
  // also fire this card's onClick.
  const interactive = Boolean(onClick);

  return (
    <div
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick?.();
              }
            }
          : undefined
      }
      className={cn(
        "flex w-full items-start justify-between gap-3 border-b pb-3 text-left last:border-0 last:pb-0",
        interactive && "cursor-pointer"
      )}
    >
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium">{title}</p>
          {priority && (
            <Badge variant={priorityBadgeVariant[priority.tone]} className={priorityClassName[priority.tone]}>
              {priority.label}
            </Badge>
          )}
        </div>
        {summary && (
          <p className="text-sm text-muted-foreground">
            <ExpandableText text={summary} maxLength={120} />
          </p>
        )}
        {metadata && <p className="text-xs text-muted-foreground">{metadata}</p>}
      </div>
      {onClick && <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
    </div>
  );
}
