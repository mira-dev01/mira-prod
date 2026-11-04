"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { OptionCard } from "@/components/option-card";
import { api, ApiError } from "@/lib/api";
import type { AgentConfig, AgentHotSignal } from "@/lib/types";

const SIGNALS: { key: AgentHotSignal; title: string; description: string }[] = [
  { key: "availability_confirmed", title: "Their dates are free", description: "The dates the guest wants turn out to be available." },
  { key: "pricing_requested", title: "They ask for the price", description: "The guest wants a quote for a stay." },
  { key: "negotiation_initiated", title: "They ask for a discount", description: "The guest starts negotiating the price." },
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
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-medium">What makes a lead hot</h2>
        <p className="text-sm text-muted-foreground">
          A guest who agrees to book is always marked very hot. Pick what else counts.
        </p>
      </div>

      <div className="space-y-2">
        {SIGNALS.map(({ key, title, description }) => (
          <OptionCard
            key={key}
            checked={hotOn.includes(key)}
            onCheckedChange={(on) => toggle(key, on)}
            title={title}
            description={description}
          />
        ))}
      </div>

      <Button onClick={save} disabled={!dirty || saving}>
        {saving ? "Saving…" : "Save"}
      </Button>
    </div>
  );
}
