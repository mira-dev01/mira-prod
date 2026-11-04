"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { InfoTip } from "@/components/ui/info-tip";
import { OptionCard } from "@/components/option-card";
import { SegmentedChoice } from "@/components/segmented-choice";
import { api, ApiError } from "@/lib/api";
import type { AgentCollectConfig, AgentConfig, AgentFieldMode } from "@/lib/types";

const FIELDS: { key: keyof AgentCollectConfig; title: string; description: string }[] = [
  { key: "dates", title: "Stay dates", description: "Check-in and check-out, or how many nights." },
  { key: "guests", title: "Number of guests", description: "So Mira only suggests places that fit." },
  { key: "purpose", title: "Reason for the stay", description: "Why they are coming, so Mira can match what they need." },
  { key: "location", title: "Preferred area", description: "Where they would like to stay." },
  { key: "budget", title: "Budget", description: "What they would like to spend." },
  { key: "email", title: "Email address", description: "Only if you want it for confirmations." },
];

export function CollectStep({ config, onSaved }: { config: AgentConfig; onSaved: (next: AgentConfig) => void }) {
  const [collect, setCollect] = useState<AgentCollectConfig>(config.collect);
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(collect) !== JSON.stringify(config.collect);

  function setMode(key: keyof AgentCollectConfig, mode: AgentFieldMode) {
    setCollect((current) => ({ ...current, [key]: mode }));
  }

  async function save() {
    setSaving(true);
    try {
      const result = await api.agentConfig.update({ collect });
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
      <div className="flex items-center gap-1.5">
        <h2 className="text-base font-medium">What Mira asks guests</h2>
        <InfoTip label="About required details">
          <p>
            <strong>Must have</strong> means Mira won&apos;t move on until the guest answers. She asks at most
            twice, and the lead is always saved either way.
          </p>
        </InfoTip>
      </div>

      <div className="space-y-2">
        <OptionCard
          checked
          disabled
          title="Name and phone"
          badge="Always asked"
          description="Needed to follow up with every guest."
        />
        {FIELDS.map(({ key, title, description }) => (
          <OptionCard
            key={key}
            checked={collect[key] !== "off"}
            onCheckedChange={(on) => setMode(key, on ? "optional" : "off")}
            title={title}
            description={description}
          >
            <SegmentedChoice
              label={`${title}: how important`}
              value={collect[key] === "required" ? "required" : "optional"}
              onChange={(mode) => setMode(key, mode)}
              options={[
                { value: "optional", label: "Nice to have" },
                { value: "required", label: "Must have" },
              ]}
            />
          </OptionCard>
        ))}
      </div>

      <Button onClick={save} disabled={!dirty || saving}>
        {saving ? "Saving…" : "Save"}
      </Button>
    </div>
  );
}
