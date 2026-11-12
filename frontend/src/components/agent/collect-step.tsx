"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InfoTip } from "@/components/ui/info-tip";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { OptionCard } from "@/components/option-card";
import { Sheet, SheetFooter, SheetHeading, WideCell } from "@/components/ui/sheet";
import { SegmentedChoice } from "@/components/segmented-choice";
import { api, ApiError } from "@/lib/api";
import type { AgentCollectConfig, AgentConfig, AgentCustomField, AgentCustomKind, AgentFieldMode, BuiltInField } from "@/lib/types";

// The server keeps at most this many of the host's own details.
const MAX_CUSTOM = 10;
const KIND_LABEL: Record<AgentCustomKind, string> = { text: "Text", time: "Time", number: "Number", yes_no: "Yes / No" };

const FIELDS: { key: BuiltInField; title: string }[] = [
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

  function setMode(key: BuiltInField, mode: AgentFieldMode) {
    setCollect((current) => ({ ...current, [key]: mode }));
  }

  function setCustom(index: number, change: Partial<AgentCustomField>) {
    setCollect((current) => ({
      ...current,
      custom: current.custom.map((field, i) => (i === index ? { ...field, ...change } : field)),
    }));
  }

  function addCustom() {
    setCollect((current) => ({
      ...current,
      custom: [...current.custom, { id: "", label: "", kind: "text", mode: "optional" }],
    }));
  }

  function removeCustom(index: number) {
    setCollect((current) => ({ ...current, custom: current.custom.filter((_, i) => i !== index) }));
  }

  // Said before saving, so a host sees the problem next to the field instead of as a server error.
  const labels = collect.custom.map((field) => field.label.trim().toLowerCase());
  const customProblem = collect.custom.some((field) => field.label.trim().length < 2)
    ? "Give every detail a name of at least 2 characters."
    : labels.some((label, i) => labels.indexOf(label) !== i)
      ? "Two details have the same name."
      : null;

  async function save() {
    if (customProblem) {
      toast.error(customProblem);
      return;
    }
    setSaving(true);
    try {
      const result = await api.agentConfig.update({
        collect: { ...collect, custom: collect.custom.map((field) => ({ ...field, label: field.label.trim() })) },
      });
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

        <SheetHeading title="Your own details" />
        <div className="col-span-12 space-y-3 px-5 py-4">
          {collect.custom.length === 0 && <p className="text-sm text-muted-foreground">Nothing added yet.</p>}
          {collect.custom.map((field, index) => (
            <div key={index} className="flex flex-wrap items-center gap-2 border-b border-border pb-3 last:border-0 last:pb-0">
              <Input
                aria-label="Detail name"
                placeholder="e.g. Arrival time"
                value={field.label}
                maxLength={60}
                className="w-56"
                onChange={(e) => setCustom(index, { label: e.target.value })}
              />
              <Select value={field.kind} onValueChange={(v) => v && setCustom(index, { kind: v as AgentCustomKind })}>
                <SelectTrigger className="w-32" aria-label="Kind of answer">
                  <SelectValue>{KIND_LABEL[field.kind]}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="text">Text</SelectItem>
                  <SelectItem value="time">Time</SelectItem>
                  <SelectItem value="number">Number</SelectItem>
                  <SelectItem value="yes_no">Yes / No</SelectItem>
                </SelectContent>
              </Select>
              <SegmentedChoice
                label={`${field.label || "Detail"}: how important`}
                value={field.mode}
                onChange={(mode) => setCustom(index, { mode })}
                options={[
                  { value: "optional", label: "Nice to have" },
                  { value: "required", label: "Must have" },
                ]}
              />
              <Button type="button" size="icon-sm" variant="ghost" aria-label="Remove detail" onClick={() => removeCustom(index)}>
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
          {customProblem && <p className="text-[13px] text-destructive">{customProblem}</p>}
          <Button type="button" size="sm" variant="outline" disabled={collect.custom.length >= MAX_CUSTOM} onClick={addCustom}>
            <Plus className="size-4" /> Add a detail
          </Button>
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
