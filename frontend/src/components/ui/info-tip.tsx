"use client";

import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/**
 * A small ⓘ that reveals a short explanation -- on hover for mouse users,
 * and on tap/click or keyboard focus for everyone else (a plain title=
 * tooltip is invisible on touch). Used for feature descriptions and
 * analytics formulas, so the page itself stays uncluttered.
 */
export function InfoTip({
  label,
  children,
  className,
}: {
  /** What this explains, for screen readers: "About Occupancy". */
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={150}
        aria-label={label}
        className={cn(
          "inline-flex size-5 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
          className
        )}
      >
        <Info className="size-3.5" />
      </PopoverTrigger>
      <PopoverContent side="top" className="w-64 gap-1.5 text-xs leading-relaxed">
        {children}
      </PopoverContent>
    </Popover>
  );
}
