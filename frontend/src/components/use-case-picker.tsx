"use client";

import { OptionCard } from "@/components/option-card";
import { USE_CASES } from "@/lib/use-cases";
import type { UseCaseKey } from "@/lib/types";

/**
 * The multi-select "what matters to you" list, shared between onboarding
 * Step 0 and the Profile page's edit control so there is exactly one place
 * that renders this picker. Nothing here is exclusive; toggling a row is
 * purely additive/removable.
 */
export function UseCasePicker({
  selected,
  onToggle,
}: {
  selected: UseCaseKey[];
  onToggle: (key: UseCaseKey) => void;
}) {
  return (
    <div>
      {USE_CASES.map((useCase) => (
        <OptionCard
          key={useCase.key}
          checked={selected.includes(useCase.key)}
          onCheckedChange={() => onToggle(useCase.key)}
          title={useCase.label}
        />
      ))}
    </div>
  );
}
