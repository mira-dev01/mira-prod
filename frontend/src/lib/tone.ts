/**
 * Single source of truth for "what color means what" across the app --
 * StatusChip, ActionableCard's priority badge, and Calendar's booking-block
 * legend all resolve through this file instead of each hand-rolling their
 * own tone->className/color map. Add a new semantic tone here once; every
 * consumer picks it up.
 */

export type StatusTone = "live" | "pending" | "progress" | "destructive" | "low" | "neutral";

/** Badge className override (pairs with variant="outline" from toneBadgeVariant). */
export const toneClassName: Record<StatusTone, string> = {
  live: "badge-status-live",
  pending: "badge-status-pending",
  progress: "badge-status-progress",
  destructive: "",
  low: "badge-priority-low",
  neutral: "",
};

/** Small leading dot glyph color (StatusChip). */
export const toneDotClassName: Record<StatusTone, string> = {
  live: "bg-(--status-live)",
  pending: "bg-(--status-pending)",
  progress: "bg-(--status-progress)",
  destructive: "bg-destructive",
  low: "bg-(--priority-low)",
  neutral: "bg-muted-foreground",
};

/** Underlying shadcn Badge variant -- only "destructive" needs its own variant, everything else rides the outline variant + a className override. */
export const toneBadgeVariant: Record<StatusTone, "outline" | "destructive"> = {
  live: "outline",
  pending: "outline",
  progress: "outline",
  destructive: "destructive",
  low: "outline",
  neutral: "outline",
};

/** Raw CSS custom property for a tone's foreground color -- for non-Badge
 * consumers (e.g. Calendar's legend swatches / booking-block fills) that
 * need the color itself, not a badge. Falls back to --muted-foreground for
 * "neutral" and "destructive" reuses the shared --destructive token. */
export const toneCssVar: Record<StatusTone, string> = {
  live: "var(--status-live)",
  pending: "var(--status-pending)",
  progress: "var(--status-progress)",
  destructive: "var(--destructive)",
  low: "var(--priority-low)",
  neutral: "var(--muted-foreground)",
};
