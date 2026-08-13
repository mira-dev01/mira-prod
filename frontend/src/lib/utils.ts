import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Frosted-glass card treatment for the Overview page's ambient-gradient
// zone (page.tsx + StatCard, LiveRequestsCard, OpportunitiesCard,
// UnansweredQuestionsCard's `glass` props) -- centralized so every glass
// surface on that page gets the same opacity/blur/highlight recipe instead
// of five copies drifting apart. Only meaningful over something worth
// blurring (the page's own gradient mesh); other consumers of these
// components leave `glass` unset and stay fully opaque.
//
// Base tint is card color with a little foreground (ink) mixed in before
// the transparency is applied -- straight bg-card/NN can only ever get
// lighter as opacity drops (there's nothing dark behind it in this warm
// palette to reveal), so darkening the *color itself* is what actually
// reads as "darker glass" rather than just "more/less see-through".
export const glassCardClassName =
  "bg-[color-mix(in_oklch,color-mix(in_oklch,var(--card)_88%,var(--foreground)_12%)_58%,transparent)] ring-1 ring-white/50 backdrop-blur-xl shadow-[inset_0_1px_0_rgba(255,255,255,0.5),0_1px_2px_rgba(42,36,32,0.06)]"

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
