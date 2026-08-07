"use client";

import { useMemo, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { PropertyOut } from "@/lib/types";

// Type-to-filter property picker. Some hosts have 10+ properties with long,
// similar-looking Airbnb-imported names (e.g. "Glasshouse Studio" repeated
// across several units), so a plain click-open <Select> forces scrolling
// through the full list. This lets the host type to narrow it down.
//
// Deliberately not built on the Popover primitive -- same reasoning as
// GuestCombobox (see guest-combobox.tsx): Popover's Trigger merges button
// semantics onto whatever it renders, which breaks a plain text <Input>.
export function PropertyCombobox({
  properties,
  value,
  onChange,
  disabled,
  placeholder = "Search property",
}: {
  properties: PropertyOut[];
  value: string;
  onChange: (propertyId: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const selectedName = properties.find((p) => p.id === value)?.name ?? "";

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return properties.slice(0, 8);
    return properties.filter((p) => p.name.toLowerCase().includes(q)).slice(0, 8);
  }, [properties, query]);

  return (
    <div ref={containerRef} className="relative">
      <Input
        value={open ? query : selectedName}
        placeholder={placeholder}
        autoComplete="off"
        disabled={disabled}
        className="truncate"
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => {
          setQuery("");
          setOpen(true);
        }}
        onBlur={(e) => {
          // Let a mousedown on a dropdown item register (see onMouseDown
          // below) before the dropdown disappears.
          if (!containerRef.current?.contains(e.relatedTarget as Node)) setOpen(false);
        }}
      />
      {open && matches.length > 0 && (
        <div className="absolute top-full left-0 z-50 mt-1 w-full min-w-[16rem] rounded-lg border bg-popover p-1 text-popover-foreground shadow-md ring-1 ring-foreground/10">
          {matches.map((property) => (
            <button
              key={property.id}
              type="button"
              className={cn(
                "block w-full truncate rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-accent",
                property.id === value && "bg-accent",
              )}
              title={property.name}
              onMouseDown={(e) => {
                // mousedown (not click) so this fires before the input's
                // onBlur can close the dropdown first.
                e.preventDefault();
                onChange(property.id);
                setQuery("");
                setOpen(false);
              }}
            >
              {property.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
