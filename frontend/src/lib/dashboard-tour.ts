/**
 * The first-visit dashboard tour: six stops, each a page in the sidebar, in the order a host meets them.
 * Copy follows the Few Words rule (DESIGN.md): what the page is for, in one line.
 */
export type TourStep = {
  /** The sidebar link this step points at (its href). */
  href: string;
  title: string;
  body: string;
};

export const TOUR_STEPS: TourStep[] = [
  {
    href: "/dashboard",
    title: "Start here",
    body: "What needs you today, and any guest on a call right now.",
  },
  {
    href: "/dashboard/properties",
    title: "Teach Mira your places",
    body: "Add each property with photos and an FAQ. A card shows what Mira still doesn't know.",
  },
  {
    href: "/dashboard/agent",
    title: "Set how Mira talks",
    body: "Her greeting, what she asks guests, and when she hands a call to you.",
  },
  {
    href: "/dashboard/calls",
    title: "Every call, kept",
    body: "A summary and the full transcript of each call.",
  },
  {
    href: "/dashboard/leads",
    title: "Guests ready to book",
    body: "Follow up here, or take over a live call.",
  },
  {
    href: "/dashboard/settings",
    title: "Numbers, alerts, billing",
    body: "Your account and team. Replay this tour any time from Profile.",
  },
];

export const TOUR_EVENT = "mira:start-tour";

/** Starts the tour from anywhere in the dashboard (the Profile page's Replay). */
export function startDashboardTour(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(TOUR_EVENT));
}

// Remembered in this browser, per account. If storage is blocked the tour simply offers itself again.
const seenKey = (userId: string) => `mira:tour-seen:${userId}`;

export function tourSeen(userId: string): boolean {
  try {
    return window.localStorage.getItem(seenKey(userId)) === "1";
  } catch {
    return false;
  }
}

export function markTourSeen(userId: string): void {
  try {
    window.localStorage.setItem(seenKey(userId), "1");
  } catch {
    // nothing to remember it with
  }
}
