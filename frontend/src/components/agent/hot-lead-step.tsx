"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { FieldRow, Sheet, SheetFooter, WideCell } from "@/components/ui/sheet";
import { api, ApiError } from "@/lib/api";
import type { AgentConfig, AgentHotSignal } from "@/lib/types";

const SIGNALS: { key: AgentHotSignal; title: string }[] = [
  { key: "availability_confirmed", title: "Their dates are free" },
  { key: "pricing_requested", title: "They ask for the price" },
  { key: "negotiation_initiated", title: "They ask for a discount" },
];

export function HotLeadStep({ config, onSaved }: { config: AgentConfig; onSaved: (next: AgentConfig) => void }) {
  const [hotOn, setHotOn] = useState<AgentHotSignal[]>(config.hot_lead.hot_on);
  const [saving, setSaving] = useState(false);
  const dirty = [...hotOn].sort().join() !== [...config.hot_lead.hot_on].sort().join();

  function toggle(key: AgentHotSignal, on: boolean) {
    setHotOn((current) => (on ? [...current, key] : current.filter((existing) => existing !== key)));
  }

  async function save() {
    setSaving(true);
    try {
      // Rules a host set up before this UI was simplified are kept as-is --
      // only the built-in signals above are editable here now.
      const result = await api.agentConfig.update({ hot_lead: { hot_on: hotOn, rules: config.hot_lead.rules } });
      onSaved(result.config);
      toast.success("Saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet>
      <WideCell>
        <FieldRow label="What makes a lead hot" hint="A guest who agrees to book is always very hot.">
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {SIGNALS.map(({ key, title }) => (
              <label key={key} className="flex cursor-pointer items-center gap-2 text-sm">
                <Checkbox checked={hotOn.includes(key)} onCheckedChange={(next) => toggle(key, Boolean(next))} />
                {title}
              </label>
            ))}
          </div>
        </FieldRow>

        <SheetFooter>
          <Button variant="cta" onClick={save} disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </SheetFooter>
      </WideCell>
    </Sheet>
  );
}
