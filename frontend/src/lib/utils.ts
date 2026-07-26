import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Matches app/services/call_service.py's BROWSER_TEST_CALLER_NUMBER -- the
// placeholder caller identity used by the dashboard's "test in browser"
// feature, since there's no real phone number for a WebRTC test call.
export const BROWSER_TEST_CALLER_NUMBER = "browser-test"

export function isBrowserTestIdentity(value: string | null | undefined): boolean {
  return value === BROWSER_TEST_CALLER_NUMBER
}

// Shared client-side "search across every column" filter -- used by the
// Calls/Properties/FAQ/Live Requests pages' search boxes. `fields` is
// whatever values that row's search box should match against (nullish
// entries and arrays of strings are both fine, so callers can pass e.g.
// [lead.guest_name, lead.phone, ...lead.properties_discussed] directly
// without pre-filtering). Case-insensitive substring match, same as every
// other filter box in the dashboard (status Select, call-type Select, etc.)
// being a plain inclusive filter rather than a strict/fuzzy search.
export function matchesSearch(query: string, fields: (string | null | undefined)[]): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return fields.some((field) => field != null && field.toLowerCase().includes(q))
}
