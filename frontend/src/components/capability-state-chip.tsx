import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { cn } from "@/lib/utils";
import type { CapabilityRequirement, CapabilityState } from "@/lib/types";

const STATE_LABEL: Record<CapabilityState, string> = {
  ready: "Ready",
  needs_setup: "Needs setup",
  disabled: "Off",
  available: "Available",
  unavailable: "Unavailable",
};

const STATE_TONE: Record<CapabilityState, StatusTone> = {
  ready: "live",
  needs_setup: "pending",
  disabled: "neutral",
  available: "neutral",
  unavailable: "low",
};

/** One capability's readiness, shared by Settings > Features & modules and
 * the onboarding review step so both read the same way. */
export function CapabilityStateChip({ state }: { state: CapabilityState }) {
  return <StatusChip status={STATE_LABEL[state]} tone={STATE_TONE[state]} />;
}

/**
 * What the host has to do, in two clearly separated kinds:
 * - "Setup needed" (amber): a requirement they can fix, with a link to fix it;
 *   "Optional" requirements stay muted.
 * - "Blocked" (red): why a switch can't change right now.
 * Shared by Features & modules and the onboarding review.
 */
export function CapabilityActions({
  requirements,
  blockedReason,
  linkActions = true,
  blockedLabel = "Blocked.",
}: {
  requirements: CapabilityRequirement[];
  blockedReason?: string | null;
  /** Off during onboarding, where dashboard links would leave the wizard. */
  linkActions?: boolean;
  /** Lead-in for blockedReason. Features & modules says what is blocked
   * ("Can't turn off yet.") so a Ready feature never also reads "Blocked". */
  blockedLabel?: string;
}) {
  const missing = requirements.filter((r) => !r.met);
  if (missing.length === 0 && !blockedReason) return null;
  // The feature's state chip already says "Needs setup"; here each missing
  // requirement is one plain line in ink with a small marker (amber = still
  // to do, grey = optional), and the way to fix it is a blue action link.
  return (
    <ul className="space-y-1.5 text-[13px]">
      {missing.map((r) => (
        <li key={r.id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <span className="flex min-w-0 items-center gap-2">
            <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", r.hard ? "bg-status-pending" : "bg-muted-foreground")} />
            {!r.hard && <span className="shrink-0 font-semibold text-muted-foreground">Optional</span>}
            <span className="text-foreground">{r.label}</span>
          </span>
          {linkActions && (
            <Link href={r.action_route} className="flex shrink-0 items-center gap-1 font-semibold text-primary hover:underline">
              {r.action_label}
              <ArrowRight className="size-3" />
            </Link>
          )}
        </li>
      ))}
      {blockedReason && (
        <li className="flex items-start gap-2">
          <span aria-hidden className="mt-1.5 size-1.5 shrink-0 rounded-full bg-destructive" />
          <span className="min-w-0 flex-1 text-foreground">
            <span className="font-semibold text-destructive">{blockedLabel} </span>
            {blockedReason.replace(/ -- /g, " — ")}
          </span>
        </li>
      )}
    </ul>
  );
}
