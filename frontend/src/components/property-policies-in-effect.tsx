"use client";

import type { PolicyOut, PolicyOverview } from "@/lib/types";

const OTHER = "other";

/** What one property follows, as the server worked it out (host-wide policies that are about other properties are not in it). */
export function effectivePolicies(overview: PolicyOverview, propertyId: string): PolicyOut[] {
  const ids = new Set(overview.properties.find((p) => p.id === propertyId)?.policy_ids ?? []);
  return overview.rows.flatMap((row) => row.items).filter((p) => ids.has(p.id));
}

/** Small tags such as "Parties welcome" / "No pets", derived from the property's approved policies. */
export function PolicyLabelChips({ overview, propertyId }: { overview: PolicyOverview | null; propertyId: string }) {
  const labels = overview?.properties.find((p) => p.id === propertyId)?.labels ?? [];
  if (labels.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {labels.map((label) => (
        <span key={label.key} className="rounded-full border border-[color-mix(in_srgb,var(--accent-warm)_55%,transparent)] px-2 py-0.5 text-xs text-muted-foreground">
          {label.label}
        </span>
      ))}
    </div>
  );
}

/**
 * Read-only: the approved policies Mira actually uses for this property. The
 * Policies tab is the one place they are edited, so this can never disagree
 * with it; where a policy covers the same ground as the house rules or
 * check-in times above, the policy is what guests hear.
 */
export function PropertyPoliciesInEffect({
  overview,
  propertyId,
  onOpenPolicies,
}: {
  overview: PolicyOverview | null;
  propertyId: string;
  onOpenPolicies: () => void;
}) {
  if (!overview) return null;
  const policies = effectivePolicies(overview, propertyId);
  const label = (p: PolicyOut) => (p.topic === OTHER ? p.title : (overview.topics.find((t) => t.key === p.topic)?.label ?? p.topic));

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-micro text-muted-foreground">Policies Mira follows here</p>
        <button type="button" onClick={onOpenPolicies} className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
          Manage policies
        </button>
      </div>
      <PolicyLabelChips overview={overview} propertyId={propertyId} />
      {policies.length === 0 ? (
        <p className="text-xs text-muted-foreground">No approved policies apply to this property yet.</p>
      ) : (
        <ul>
          {policies.map((p) => (
            <li key={p.id} className="border-b border-[color-mix(in_srgb,var(--accent-warm)_55%,transparent)] py-2.5 last:border-b-0">
              <p className="text-xs text-muted-foreground">
                {label(p)} · {p.property_id ? "This property only" : "All properties"}
              </p>
              <p className="text-sm">{p.text}</p>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">
        Where a policy covers the same topic as the house rules, imported FAQs or check-in times, Mira uses the policy. Your imported FAQs are kept.
      </p>
    </div>
  );
}
