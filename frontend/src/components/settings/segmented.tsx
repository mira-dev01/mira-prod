"use client";

import { cn } from "@/lib/utils";

/** A short either/or choice (2-3 options) drawn as one ruled control with the
 * selected option filled. Used wherever Settings asks "which of these?". */
export function Segmented<T extends string>({
  label,
  value,
  onChange,
  options,
  disabled,
  className,
}: {
  /** Accessible name for the group. */
  label: string;
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className={cn("flex rounded-md border border-foreground p-0.5", className)}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          disabled={disabled}
          onClick={() => onChange(option.value)}
          className={cn(
            "min-h-8 min-w-0 flex-1 rounded-[3px] px-3 py-1.5 text-[13px] transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:opacity-60",
            value === option.value ? "bg-primary font-semibold text-primary-foreground" : "text-foreground hover:bg-accent"
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
