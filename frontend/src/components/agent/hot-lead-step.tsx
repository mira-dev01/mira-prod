"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { OptionCard } from "@/components/option-card";
import { Sheet, SheetFooter, SheetHeading, WideCell } from "@/components/ui/sheet";
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
      const result = await api.agentConfig.update({ hot_lead: { hot_on: hotOn } });
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
        <SheetHeading title="What makes a lead hot">A guest who agrees to book is always very hot.</SheetHeading>
        <div className="col-span-12 px-5 py-4">
          {SIGNALS.map(({ key, title }) => (
            <OptionCard key={key} checked={hotOn.includes(key)} onCheckedChange={(on) => toggle(key, on)} title={title} />
          ))}
        </div>
        <SheetFooter>
          <Button variant="cta" onClick={save} disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </SheetFooter>
      </WideCell>
    </Sheet>
  );
}
