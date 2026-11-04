"use client";

import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

/**
 * A setting the host switches on with a checkbox. A one-line description says
 * what it does; anything it needs (a mode, an amount) is passed as children
 * and only shows while it is checked, so the page stays short until a host
 * opts in. Used for every on/off choice on the Agent page.
 */
export function OptionCard({
  checked,
  onCheckedChange,
  title,
  description,
  badge,
  disabled,
  children,
}: {
  checked: boolean;
  onCheckedChange?: (checked: boolean) => void;
  title: string;
  description: string;
  /** A small tag after the title, e.g. "Always on". */
  badge?: string;
  disabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn("rounded-lg border p-3 transition-colors", checked && "border-primary/40 bg-primary/[0.03]")}>
      <label className={cn("flex items-start gap-3", disabled ? "cursor-default" : "cursor-pointer")}>
        <Checkbox
          checked={checked}
          disabled={disabled}
          onCheckedChange={(next) => onCheckedChange?.(Boolean(next))}
          className="mt-0.5"
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{title}</span>
            {badge && (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                {badge}
              </span>
            )}
          </span>
          <span className="block text-xs text-muted-foreground">{description}</span>
        </span>
      </label>
      {checked && children && <div className="mt-3 pl-7">{children}</div>}
    </div>
  );
}
