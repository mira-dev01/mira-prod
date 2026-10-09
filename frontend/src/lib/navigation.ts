import { createElement } from "react";
import {
  BrainCircuit,
  Building2,
  Calendar,
  ChartColumn,
  Circle,
  HelpCircle,
  Home,
  Phone,
  Settings,
  UserRound,
  Users,
  Zap,
  type LucideIcon,
} from "lucide-react";
import type { NavItem } from "@/lib/types";

// The sidebar's destinations, order and eligibility come from the backend
// (app/services/navigation_registry.py via GET /preferences/navigation).
// This file only holds what's purely presentational -- icons -- plus the
// default list used if that request fails, so a network error never leaves
// a host without navigation. backend/tests/test_ui_preferences.py checks
// the ids/hrefs here stay in sync with the registry.
export const NAV_ICONS: Record<string, LucideIcon> = {
  overview: Home,
  analytics: ChartColumn,
  properties: Building2,
  ai_training: BrainCircuit,
  calendar: Calendar,
  calls: Phone,
  live_requests: Users,
  opportunities: Zap,
  guests: UserRound,
  faq: HelpCircle,
  settings: Settings,
};

/** A destination's icon by id (Circle for an id with no icon yet). A
 * component rather than a lookup function, so callers never create a
 * component type during render. */
export function NavIcon({ id, className }: { id: string; className?: string }) {
  return createElement(NAV_ICONS[id] ?? Circle, { className });
}

function fallback(id: string, label: string, href: string, placement: NavItem["placement"], hideable = true): NavItem {
  return { id, label, href, placement, hideable, available: true, hidden: false, capabilities: [], unavailable_reason: null };
}

// Same order as the registry's defaults (= the pre-customization sidebar).
export const FALLBACK_NAV: NavItem[] = [
  fallback("overview", "Overview", "/dashboard", "pinned_top", false),
  fallback("analytics", "Analytics", "/dashboard/analytics", "movable"),
  fallback("properties", "Properties", "/dashboard/properties", "movable", false),
  fallback("ai_training", "AI Training", "/dashboard/properties/ai-training", "movable"),
  fallback("calendar", "Calendar", "/dashboard/calendar", "movable"),
  fallback("calls", "Calls", "/dashboard/calls", "movable"),
  fallback("live_requests", "Live Requests", "/dashboard/leads", "movable"),
  fallback("opportunities", "Opportunities", "/dashboard/opportunities", "movable"),
  fallback("guests", "Guests", "/dashboard/guests", "movable"),
  fallback("faq", "FAQ", "/dashboard/faq", "movable"),
  fallback("settings", "Settings", "/dashboard/settings", "pinned_bottom", false),
];

/** The one nav item a pathname belongs to: the longest matching href, so
 * /dashboard/properties/ai-training highlights AI Training, not Properties,
 * and /dashboard only matches exactly. */
export function activeNavId(items: NavItem[], pathname: string): string | null {
  let best: NavItem | null = null;
  for (const item of items) {
    const matches =
      item.href === "/dashboard"
        ? pathname === item.href
        : pathname === item.href || pathname.startsWith(`${item.href}/`);
    if (matches && (!best || item.href.length > best.href.length)) best = item;
  }
  return best?.id ?? null;
}
