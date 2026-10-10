import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { StatusChip, type StatusTone } from "@/components/status-chip";
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
}: {
  requirements: CapabilityRequirement[];
  blockedReason?: string | null;
  /** Off during onboarding, where dashboard links would leave the wizard. */
  linkActions?: boolean;
}) {
  const missing = requirements.filter((r) => !r.met);
  if (missing.length === 0 && !blockedReason) return null;
  return (
    <ul className="space-y-1.5 text-xs">
      {missing.map((r) => (
        <li key={r.id} className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span className="flex min-w-0 items-center gap-2">
            {r.hard ? (
              <StatusChip status="Setup needed" tone="pending" className="shrink-0 normal-case" />
            ) : (
              <span className="shrink-0 font-medium text-muted-foreground">Optional</span>
            )}
            <span className={r.hard ? "text-foreground" : "text-muted-foreground"}>{r.label}</span>
          </span>
          {linkActions && (
            <Link
              href={r.action_route}
              className="flex shrink-0 items-center gap-1 font-medium text-(--status-pending-strong) hover:underline"
            >
              {r.action_label}
              <ArrowRight className="size-3" />
            </Link>
          )}
        </li>
      ))}
      {blockedReason && (
        <li className="flex flex-wrap items-start gap-2">
          <StatusChip status="Blocked" tone="destructive" className="shrink-0 normal-case" />
          <span className="min-w-0 flex-1 text-destructive">{blockedReason}</span>
        </li>
      )}
    </ul>
  );
}
