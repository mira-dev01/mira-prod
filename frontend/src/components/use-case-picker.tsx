"use client";

import { Checkbox } from "@/components/ui/checkbox";
import { USE_CASES } from "@/lib/use-cases";
import type { UseCaseKey } from "@/lib/types";

/**
 * The multi-select "what matters to you" card grid -- shared between
 * onboarding Step 0 and the Profile page's edit control (Flow 4 of the
 * dashboard/onboarding personalization plan) so there is exactly one
 * place that renders this picker. Nothing here is exclusive; toggling a
 * card is purely additive/removable.
 */
export function UseCasePicker({
  selected,
  onToggle,
}: {
  selected: UseCaseKey[];
  onToggle: (key: UseCaseKey) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {USE_CASES.map((useCase) => {
        const checked = selected.includes(useCase.key);
        return (
          <label
            key={useCase.key}
            className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors duration-150 ${
              checked ? "border-primary bg-accent" : "border-input hover:bg-accent/50"
            }`}
          >
            <Checkbox checked={checked} onCheckedChange={() => onToggle(useCase.key)} className="mt-0.5" />
            <span className="space-y-0.5">
              <span className="block text-sm font-medium">{useCase.label}</span>
              <span className="block text-xs text-muted-foreground">{useCase.description}</span>
            </span>
          </label>
        );
      })}
    </div>
  );
}
