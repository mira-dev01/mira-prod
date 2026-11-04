"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** A short either/or choice shown as a row of buttons (2-3 options). */
export function SegmentedChoice<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (next: T) => void;
  options: { value: T; label: string }[];
  /** Accessible name for the group. */
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-2">
      {options.map((option) => (
        <Button
          key={option.value}
          type="button"
          size="sm"
          variant={value === option.value ? "default" : "outline"}
          aria-pressed={value === option.value}
          className={cn(value === option.value && "pointer-events-none")}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  );
}
