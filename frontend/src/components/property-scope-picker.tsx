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

export function scopeLabel(
  scope: PropertyScope,
  properties: PropertyOption[] | null | undefined,
  inclusive = false,
): string {
  if (scope.all) return "All properties";
  // With most properties ticked, "All except Casa Blu" says it better than "A +4".
  if (inclusive && properties) {
    const left = properties.filter((property) => !scope.ids.includes(property.id));
    if (left.length > 0 && left.length <= 2 && left.length < scope.ids.length) return `All except ${left.map((p) => p.name).join(", ")}`;
  }
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
  inclusive = false,
  variant = "outline",
}: {
  value: PropertyScope;
  onChange: (next: PropertyScope) => void;
  properties: PropertyOption[] | null | undefined;
  id?: string;
  /**
   * "Everything is ticked, untick what you don't want." With it, "All
   * properties" shows every property ticked and unticking one leaves the rest
   * (ticking them all again collapses back to "All properties"). Without it
   * (the FAQ screens) picking a property from "All" starts a fresh selection.
   */
  inclusive?: boolean;
  /** "link" renders the trigger as small quiet text instead of a field. */
  variant?: "outline" | "link";
}) {
  function toggleAll(checked: boolean) {
    // "All" is a mode, not a list: picking it clears specific properties, and
    // unticking it with nothing else chosen is ignored so the scope is never empty.
    if (checked) onChange(ALL_PROPERTIES);
  }

  function toggleProperty(propertyId: string, checked: boolean) {
    const everyone = (properties ?? []).map((property) => property.id);
    const base = value.all ? (inclusive ? everyone : []) : value.ids;
    const ids = checked ? [...new Set([...base, propertyId])] : base.filter((existing) => existing !== propertyId);
    if (ids.length === 0) return;
    if (inclusive && everyone.length > 0 && everyone.every((each) => ids.includes(each))) {
      onChange(ALL_PROPERTIES);
      return;
    }
    onChange({ all: false, ids });
  }

  return (
    <Popover>
      <PopoverTrigger
        render={
          variant === "link" ? (
            <button
              id={id}
              type="button"
              className="inline-flex items-center gap-1 rounded text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              <span className="truncate">{scopeLabel(value, properties, inclusive)}</span>
              <ChevronDown className="size-3 shrink-0" />
            </button>
          ) : (
            <Button id={id} type="button" variant="outline" className="w-full justify-between font-normal">
              <span className="truncate">{scopeLabel(value, properties, inclusive)}</span>
              <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
            </Button>
          )
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
              checked={(inclusive && value.all) || (!value.all && value.ids.includes(property.id))}
              onCheckedChange={(checked) => toggleProperty(property.id, Boolean(checked))}
            />
            <span className="min-w-0 truncate">{property.name}</span>
          </label>
        ))}
      </PopoverContent>
    </Popover>
  );
}
