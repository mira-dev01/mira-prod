import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Section wrapper for the property edit panel -- a micro-label title
 * (same .text-micro treatment as the plain SectionLabel used for Basic
 * info/Amenities/House rules elsewhere in this form), optional count pill
 * and header action (e.g. "+ Add note"), then the section's own content.
 * Shared by Description, Seasonal notes and FAQ so all of this form's
 * sections read as one consistent pattern rather than two competing header
 * styles bolted together.
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
    <section className={cn("space-y-3", className)}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <p className="text-micro text-muted-foreground">{title}</p>
          {count !== undefined && (
            <Badge variant="outline" className="text-primary">
              {count}
            </Badge>
          )}
        </div>
        {action}
      </div>
      {helpText && <p className="text-xs text-muted-foreground">{helpText}</p>}
      {children}
    </section>
  );
}
