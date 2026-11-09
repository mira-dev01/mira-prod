"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { InfoTip } from "@/components/ui/info-tip";
import { OptionCard } from "@/components/option-card";
import { Sheet, SheetFooter, SheetHeading, WideCell } from "@/components/ui/sheet";
import { SegmentedChoice } from "@/components/segmented-choice";
import { api, ApiError } from "@/lib/api";
import type { AgentCollectConfig, AgentConfig, AgentFieldMode } from "@/lib/types";

const FIELDS: { key: keyof AgentCollectConfig; title: string }[] = [
  { key: "dates", title: "Stay dates" },
  { key: "guests", title: "Number of guests" },
  { key: "purpose", title: "Reason for the stay" },
  { key: "location", title: "Preferred area" },
  { key: "budget", title: "Budget" },
  { key: "email", title: "Email address" },
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
    <Sheet>
      <WideCell>
        <SheetHeading
          title="What Mira asks guests"
          action={
            <InfoTip label="About required details">
              <p>
                <strong>Must have</strong> means Mira won&apos;t move on until the guest answers. She asks at most
                twice, and the lead is always saved either way.
              </p>
            </InfoTip>
          }
        />
        <div className="col-span-12 px-5 py-4">
          <OptionCard checked disabled title="Name and phone" badge="Always asked" />
          {FIELDS.map(({ key, title }) => (
            <OptionCard
              key={key}
              checked={collect[key] !== "off"}
              onCheckedChange={(on) => setMode(key, on ? "optional" : "off")}
              title={title}
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
        <SheetFooter>
          <Button variant="cta" onClick={save} disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </SheetFooter>
      </WideCell>
    </Sheet>
  );
}
