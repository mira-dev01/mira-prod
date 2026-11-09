"use client";

import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Hint } from "@/components/ui/sheet";

/**
 * A labelled on/off switch with a one-line hint, as one ruled line inside a
 * FieldRow. The whole line toggles, not just the pill.
 */
export function ToggleField({
  id,
  label,
  hint,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div
      className="flex items-center justify-between gap-4 border-b border-border py-3 first:pt-0 last:border-0 last:pb-0"
      onClick={(e) => {
        // Fallback for a confirmed-live bug: a real click directly on the
        // switch pill sometimes never reaches its own click handler (a
        // synthetic .click() on the same element always works, so this
        // isn't a logic bug in the switch itself -- more likely a
        // hit-testing/positioning quirk). Base-ui's Switch calls
        // preventDefault() the instant its own onClick runs, and a native
        // <label for> click already toggles the input directly -- skip
        // this fallback in both of those cases so a click that already
        // worked never gets double-toggled back to its original state.
        if (e.defaultPrevented || (e.target as HTMLElement).closest("label")) return;
        onChange(!checked);
      }}
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <Label htmlFor={id} className="text-sm font-medium">
          {label}
        </Label>
        {hint && <Hint>{hint}</Hint>}
      </div>
      <Switch id={id} className="shrink-0" checked={checked} onCheckedChange={onChange} />
    </div>
  );
}
