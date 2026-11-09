import { Badge } from "@/components/ui/badge";
import { SheetHeading, WideCell } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

/**
 * One block of the property form: a heading (with an optional count and a
 * header action such as "+ Add note"), a one-line hint, then the block's own
 * content. Place it inside a <Sheet>. Used by Seasonal notes, FAQ and Photos,
 * and by the field groups, so every part of the form reads the same way.
 */
export function PropertyFormSection({
  title,
  count,
  action,
  helpText,
  children,
  className,
}: {
  title: string;
  count?: number;
  action?: React.ReactNode;
  helpText?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <WideCell className={className}>
      <SheetHeading
        title={title}
        action={
          <div className="flex shrink-0 items-center gap-3">
            {count !== undefined && <Badge variant="outline">{count}</Badge>}
            {action}
          </div>
        }
      >
        {helpText}
      </SheetHeading>
      <div className={cn("col-span-12")}>{children}</div>
    </WideCell>
  );
}
