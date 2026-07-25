import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Section wrapper for the property edit panel -- an icon-letter badge, bold
 * title, optional count pill and header action (e.g. "+ Add note"), a thin
 * brand-colored rule, then the section's own content. Shared by Description,
 * Seasonal notes and FAQ so those three read as one consistent pattern
 * instead of each rolling its own header.
 */
export function PropertyFormSection({
  icon,
  title,
  count,
  action,
  helpText,
  children,
  className,
}: {
  icon: string;
  title: string;
  count?: number;
  action?: React.ReactNode;
  helpText?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("space-y-3", className)}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 font-heading text-sm font-semibold text-primary">
            {icon}
          </span>
          <h3 className="font-heading text-base font-medium">{title}</h3>
          {count !== undefined && (
            <Badge variant="outline" className="text-primary">
              {count}
            </Badge>
          )}
        </div>
        {action}
      </div>
      <div className="border-t-2 border-primary/20" />
      {helpText && <p className="text-xs text-muted-foreground">{helpText}</p>}
      {children}
    </section>
  );
}
