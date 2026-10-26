import { Calendar, ChartColumn, IndianRupee, UserRound, Users, Zap, type LucideIcon } from "lucide-react";
import type { UseCaseKey } from "@/lib/types";

// Dashboard/onboarding personalization -- the SINGLE source of truth
// mapping each use-case key to its label/description (onboarding Step 0's
// card grid, Profile's edit control) and the one sidebar nav item it
// promotes into "Your focus" (sidebar-nav.tsx). Keep this list's `key`
// values in sync with backend VALID_INTEREST_KEYS (app/schemas/user.py) --
// an unrecognized key sent to the backend is silently dropped there, and
// a key present here with no matching sidebar link would just never
// promote anything, so the two are meant to be edited together.
//
// Deliberately excludes "guest support" (answering calls, property Q&A):
// it's the always-on core product, not a differentiator -- picking it
// wouldn't change anything, so it's not offered as a choice.
export type UseCase = {
  key: UseCaseKey;
  label: string;
  description: string;
  icon: LucideIcon;
  /** The one sidebar-nav href this interest promotes to "Your focus". */
  navHref: string;
};

export const USE_CASES: UseCase[] = [
  {
    key: "inbound_leads",
    label: "Inbound leads & bookings",
    description: "Capturing guest inquiries and turning them into confirmed stays.",
    icon: Users,
    navHref: "/dashboard/leads",
  },
  {
    key: "whatsapp_automation",
    label: "WhatsApp automation",
    description: "Automatic WhatsApp follow-ups and recovery when you can't pick up a call.",
    icon: Zap,
    navHref: "/dashboard/opportunities",
  },
  {
    key: "pricing_negotiation",
    label: "Pricing & negotiation",
    description: "Letting Mira answer rate questions and handle negotiation for you.",
    icon: IndianRupee,
    navHref: "/dashboard/pricing",
  },
  {
    key: "calendar_management",
    label: "Calendar & booking management",
    description: "Keeping availability accurate and bookings reconciled across properties.",
    icon: Calendar,
    navHref: "/dashboard/calendar",
  },
  {
    key: "guest_relationships",
    label: "Guest relationships",
    description: "Tracking repeat guests and their stay history.",
    icon: UserRound,
    navHref: "/dashboard/guests",
  },
  {
    key: "portfolio_analytics",
    label: "Portfolio analytics",
    description: "Reporting and insights across your whole portfolio.",
    icon: ChartColumn,
    navHref: "/dashboard/analytics",
  },
];

export function useCaseByKey(key: string): UseCase | undefined {
  return USE_CASES.find((useCase) => useCase.key === key);
}

/** Every nav href any use case promotes, in USE_CASES' own display order --
 * used by sidebar-nav.tsx to partition its existing `links` array. */
export function hrefsForInterests(interests: readonly string[]): string[] {
  const selected = new Set(interests);
  return USE_CASES.filter((useCase) => selected.has(useCase.key)).map((useCase) => useCase.navHref);
}
