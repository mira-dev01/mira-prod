"use client";

import { useMemo, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { GuestProfileOut } from "@/lib/types";

// Type-to-filter guest picker backed by the existing guest list -- falls
// back to plain free text for a brand-new guest (no GuestProfile match
// required). Selecting an existing guest carries their phone number along
// so the resulting record links into guest memory (GuestProfile is keyed by
// phone, see backend/app/models/guest_profile.py) rather than creating an
// unrelated, unlinked name string.
//
// Deliberately not built on the Popover primitive: Popover's Trigger merges
// button semantics (type="button", click-trigger handlers) onto whatever
// it renders, which breaks a plain text <Input> (can no longer be typed
// into). This is a self-positioned absolute dropdown instead -- simpler and
// correct for a "type to search" field.
export function GuestCombobox({
  guests,
  name,
  onNameChange,
  onSelectGuest,
  placeholder = "Search or type a new guest name",
}: {
  guests: GuestProfileOut[];
  name: string;
  onNameChange: (name: string) => void;
  onSelectGuest: (guest: GuestProfileOut | null) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const matches = useMemo(() => {
    const query = name.trim().toLowerCase();
    if (!query) return guests.slice(0, 8);
    return guests.filter((g) => g.name?.toLowerCase().includes(query) || g.phone.includes(query)).slice(0, 8);
  }, [guests, name]);

  return (
    <div ref={containerRef} className="relative">
      <Input
        value={name}
        placeholder={placeholder}
        autoComplete="off"
        onChange={(e) => {
          onNameChange(e.target.value);
          onSelectGuest(null);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={(e) => {
          // Let a mousedown on a dropdown item register (see onMouseDown
          // below) before the dropdown disappears.
          if (!containerRef.current?.contains(e.relatedTarget as Node)) setOpen(false);
        }}
      />
      {open && matches.length > 0 && (
        <div className="absolute top-full left-0 z-50 mt-1 w-full rounded-lg border bg-popover p-1 text-popover-foreground shadow-md ring-1 ring-foreground/10">
          {matches.map((guest) => (
            <button
              key={guest.id}
              type="button"
              className={cn(
                "flex w-full flex-col items-start gap-0.5 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-accent",
              )}
              onMouseDown={(e) => {
                // mousedown (not click) so this fires before the input's
                // onBlur can close the dropdown first.
                e.preventDefault();
                onNameChange(guest.name ?? guest.phone);
                onSelectGuest(guest);
                setOpen(false);
              }}
            >
              <span className="font-medium">{guest.name ?? "Unnamed guest"}</span>
              <span className="text-xs text-muted-foreground">{guest.phone}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
