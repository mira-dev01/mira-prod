"use client";

import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

/**
 * A setting the host switches on with a checkbox: one ruled row inside a block,
 * not a box. Anything it needs (a mode, an amount) is passed as children and
 * only shows while it is checked, so the page stays short until a host opts in.
 * A description is optional; give one only when it states a consequence the
 * title cannot. Used for every on/off choice on the Agent page.
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
  description?: string;
  /** A small tag after the title, e.g. "Always on". */
  badge?: string;
  disabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="border-b border-border py-3 first:pt-0 last:border-0 last:pb-0">
      <label className={cn("flex items-start gap-3", disabled ? "cursor-default" : "cursor-pointer")}>
        <Checkbox
          checked={checked}
          disabled={disabled}
          onCheckedChange={(next) => onCheckedChange?.(Boolean(next))}
          className="mt-0.5"
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">{title}</span>
            {badge && (
              <span className="border border-border px-2 py-0.5 text-[11px] font-semibold text-muted-foreground">{badge}</span>
            )}
          </span>
          {description && <span className="block text-[13px] text-muted-foreground">{description}</span>}
        </span>
      </label>
      {checked && children && <div className="mt-3 pl-7">{children}</div>}
    </div>
  );
}
