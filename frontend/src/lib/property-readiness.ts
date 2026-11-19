import type { PropertyOut } from "@/lib/types";

/**
 * What Mira can say about a property. A property saves with only a name and a price, but a guest's questions
 * are answered from the rest: what the place is, what it has, its rules, the area, photos to send and the
 * FAQ. Each item is something Mira can only repeat if the host has written it down.
 */
export type ReadinessItem = { key: string; label: string; done: boolean };

export type PropertyReadiness = {
  items: ReadinessItem[];
  doneCount: number;
  total: number;
  /** Labels of what is still missing, in the order a host would fill them in. */
  missing: string[];
  complete: boolean;
};

const filled = (text: string | null | undefined) => Boolean(text && text.trim());

/** `faqCount` = FAQ answers saved for this property (the Q&A list), on top of any listed on the property itself. */
export function propertyReadiness(property: PropertyOut, faqCount: number): PropertyReadiness {
  const items: ReadinessItem[] = [
    { key: "number", label: "Call number", done: filled(property.exophone) },
    { key: "about", label: "Description", done: filled(property.usp) },
    { key: "amenities", label: "Amenities", done: (property.amenities ?? []).length > 0 },
    { key: "rules", label: "House rules", done: filled(property.house_rules) },
    { key: "area", label: "Neighbourhood", done: filled(property.neighborhood_info) },
    { key: "photos", label: "Photos", done: (property.photos ?? []).length > 0 },
    { key: "faq", label: "FAQ", done: faqCount + (property.faq ?? []).length > 0 },
  ];
  const missing = items.filter((item) => !item.done).map((item) => item.label);
  return {
    items,
    doneCount: items.length - missing.length,
    total: items.length,
    missing,
    complete: missing.length === 0,
  };
}
