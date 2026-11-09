"use client";

import { PropertyFormSection } from "@/components/property-form-section";
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
        <span key={label.key} className="border border-border px-2 py-0.5 text-xs text-muted-foreground">
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
    <PropertyFormSection
      title="Policies Mira follows here"
      action={
        <button type="button" onClick={onOpenPolicies} className="text-sm text-muted-foreground underline underline-offset-2 hover:text-foreground">
          Manage policies
        </button>
      }
      helpText="Read-only. Where a policy covers the same topic as the house rules or check-in times, Mira uses the policy."
    >
      <div className="space-y-3 p-5">
        <PolicyLabelChips overview={overview} propertyId={propertyId} />
        {policies.length === 0 ? (
          <p className="text-sm text-muted-foreground">No approved policies apply yet.</p>
        ) : (
          <ul>
            {policies.map((p) => (
              <li key={p.id} className="border-b border-border py-3 first:pt-0 last:border-b-0 last:pb-0">
                <p className="text-[13px] text-muted-foreground">
                  {label(p)} · {p.property_id ? "This property only" : "All properties"}
                </p>
                <p className="text-sm">{p.text}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PropertyFormSection>
  );
}
