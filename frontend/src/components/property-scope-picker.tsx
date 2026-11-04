"use client";

import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/** Where an answer applies: every property, or a chosen set (never empty). */
export type PropertyScope = { all: boolean; ids: string[] };

export const ALL_PROPERTIES: PropertyScope = { all: true, ids: [] };

type PropertyOption = { id: string; name: string };

/** Request fields for the FAQ endpoints (answer a gap, create, set scope). */
export function scopePayload(scope: PropertyScope): { apply_to_all?: boolean; property_ids?: string[] } {
  return scope.all ? { apply_to_all: true } : { property_ids: scope.ids };
}

/** The scope an existing FAQ currently has, from its rows (one per property). */
export function scopeFromPropertyIds(propertyIds: (string | null)[]): PropertyScope {
  if (propertyIds.length === 0 || propertyIds.some((id) => id === null)) return ALL_PROPERTIES;
  return { all: false, ids: propertyIds as string[] };
}

export function scopeLabel(scope: PropertyScope, properties: PropertyOption[] | null | undefined): string {
  if (scope.all) return "All properties";
  const names = scope.ids.map((id) => properties?.find((p) => p.id === id)?.name ?? "Property");
  if (names.length <= 1) return names[0] ?? "Choose properties";
  return `${names[0]} +${names.length - 1}`;
}

/**
 * The one "applies to" control for FAQ answers: a dropdown of checkboxes,
 * "All properties" on top. Shared by the unanswered-questions panel and the
 * FAQ add/edit panel so both behave the same.
 */
export function PropertyScopePicker({
  value,
  onChange,
  properties,
  id,
}: {
  value: PropertyScope;
  onChange: (next: PropertyScope) => void;
  properties: PropertyOption[] | null | undefined;
  id?: string;
}) {
  function toggleAll(checked: boolean) {
    // "All" is a mode, not a list: picking it clears specific properties, and
    // unticking it with nothing else chosen is ignored so the scope is never empty.
    if (checked) onChange(ALL_PROPERTIES);
  }

  function toggleProperty(propertyId: string, checked: boolean) {
    const base = value.all ? [] : value.ids;
    const ids = checked ? [...base, propertyId] : base.filter((existing) => existing !== propertyId);
    if (ids.length === 0) return;
    onChange({ all: false, ids });
  }

  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button id={id} type="button" variant="outline" className="w-full justify-between font-normal">
            <span className="truncate">{scopeLabel(value, properties)}</span>
            <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
          </Button>
        }
      />
      <PopoverContent align="start" className="max-h-72 w-72 overflow-y-auto">
        <label className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-sm font-medium hover:bg-muted">
          <Checkbox checked={value.all} onCheckedChange={(checked) => toggleAll(Boolean(checked))} />
          All properties
        </label>
        <div className="h-px bg-border" />
        {(properties ?? []).map((property) => (
          <label
            key={property.id}
            className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-muted"
          >
            <Checkbox
              checked={!value.all && value.ids.includes(property.id)}
              onCheckedChange={(checked) => toggleProperty(property.id, Boolean(checked))}
            />
            <span className="min-w-0 truncate">{property.name}</span>
          </label>
        ))}
      </PopoverContent>
    </Popover>
  );
}
