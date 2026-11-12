"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { OptionCard } from "@/components/option-card";
import { Sheet, SheetFooter, SheetHeading, WideCell } from "@/components/ui/sheet";
import { api, ApiError } from "@/lib/api";
import type { AgentConfig, AgentHotRule, AgentHotSignal } from "@/lib/types";

// The numbers a hot-lead rule can test without any custom detail (the server reads the same four names).
const BUILT_IN_FIELDS: { value: string; label: string }[] = [
  { value: "guests", label: "Number of guests" },
  { value: "nights", label: "Nights" },
  { value: "budget", label: "Budget" },
  { value: "days_until_checkin", label: "Days until check-in" },
];

// The server keeps at most this many conditions.
const MAX_RULES = 5;

const SIGNALS: { key: AgentHotSignal; title: string }[] = [
  { key: "availability_confirmed", title: "Their dates are free" },
  { key: "pricing_requested", title: "They ask for the price" },
  { key: "negotiation_initiated", title: "They ask for a discount" },
];

export function HotLeadStep({ config, onSaved }: { config: AgentConfig; onSaved: (next: AgentConfig) => void }) {
  const [hotOn, setHotOn] = useState<AgentHotSignal[]>(config.hot_lead.hot_on);
  const [rules, setRules] = useState<AgentHotRule[]>(config.hot_lead.rules);
  const [saving, setSaving] = useState(false);
  const dirty =
    [...hotOn].sort().join() !== [...config.hot_lead.hot_on].sort().join() ||
    JSON.stringify(rules) !== JSON.stringify(config.hot_lead.rules);

  // A rule can be about the built-in numbers or about a number / yes-or-no detail the host added (saved ones only).
  const customFields = config.collect.custom.filter((f) => f.kind === "number" || f.kind === "yes_no");
  const fieldOptions = [
    ...BUILT_IN_FIELDS,
    ...customFields.map((f) => ({ value: `custom:${f.id}`, label: f.label })),
  ];
  const labelOf = (field: string) => fieldOptions.find((o) => o.value === field)?.label ?? field;
  const isYesNo = (field: string) => customFields.find((f) => `custom:${f.id}` === field)?.kind === "yes_no";

  function setRule(index: number, change: Partial<AgentHotRule>) {
    setRules((current) => current.map((rule, i) => (i === index ? { ...rule, ...change } : rule)));
  }
  function setRuleField(index: number, field: string) {
    // A yes/no detail only has one meaningful test: "is Yes".
    setRule(index, isYesNo(field) ? { field, op: "eq", value: 1 } : { field, op: "gte" });
  }

  function toggle(key: AgentHotSignal, on: boolean) {
    setHotOn((current) => (on ? [...current, key] : current.filter((existing) => existing !== key)));
  }

  async function save() {
    setSaving(true);
    try {
      const result = await api.agentConfig.update({ hot_lead: { hot_on: hotOn, rules } });
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

        <SheetHeading title="Your own conditions" />
        <div className="col-span-12 space-y-3 px-5 py-4">
          {rules.length === 0 && <p className="text-sm text-muted-foreground">Nothing added yet.</p>}
          {rules.map((rule, index) => (
            <div key={index} className="flex flex-wrap items-center gap-2 border-b border-border pb-3 last:border-0 last:pb-0">
              <span className="text-sm">Hot when</span>
              <Select value={rule.field} onValueChange={(v) => v && setRuleField(index, v)}>
                <SelectTrigger className="w-48" aria-label="Detail">
                  <SelectValue>{labelOf(rule.field)}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {fieldOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {isYesNo(rule.field) ? (
                <span className="text-sm">is Yes</span>
              ) : (
                <>
                  <Select value={rule.op} onValueChange={(v) => v && setRule(index, { op: v as AgentHotRule["op"] })}>
                    <SelectTrigger className="w-40" aria-label="Comparison">
                      <SelectValue>{{ gte: "is at least", lte: "is at most", eq: "is exactly" }[rule.op]}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="gte">is at least</SelectItem>
                      <SelectItem value="lte">is at most</SelectItem>
                      <SelectItem value="eq">is exactly</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input
                    aria-label="Number"
                    type="number"
                    value={rule.value}
                    className="w-28"
                    onChange={(e) => setRule(index, { value: Number(e.target.value) })}
                  />
                </>
              )}
              <Button type="button" size="icon-sm" variant="ghost" aria-label="Remove condition" onClick={() => setRules((r) => r.filter((_, i) => i !== index))}>
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={rules.length >= MAX_RULES}
            onClick={() => setRules((r) => [...r, { field: "guests", op: "gte", value: 1 }])}
          >
            <Plus className="size-4" /> Add a condition
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
