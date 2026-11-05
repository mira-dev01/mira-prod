"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { OptionCard } from "@/components/option-card";
import { api, ApiError } from "@/lib/api";
import type { AgentBusyCalls, AgentConfig } from "@/lib/types";

/** What happens to a guest who rings while Mira is already on a call. */
export function BusyCallsStep({ config, onSaved }: { config: AgentConfig; onSaved: (next: AgentConfig) => void }) {
  const [mode, setMode] = useState<AgentBusyCalls["mode"]>(config.busy_calls.mode);
  const [minutes, setMinutes] = useState(String(config.busy_calls.callback_wait_minutes));
  const [saving, setSaving] = useState(false);

  const waitMinutes = Number(minutes);
  const validMinutes = Number.isInteger(waitMinutes) && waitMinutes >= 1 && waitMinutes <= 60;
  const dirty = mode !== config.busy_calls.mode || waitMinutes !== config.busy_calls.callback_wait_minutes;

  async function save() {
    setSaving(true);
    try {
      const result = await api.agentConfig.update({
        busy_calls: { mode, callback_wait_minutes: validMinutes ? waitMinutes : config.busy_calls.callback_wait_minutes },
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
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-medium">When Mira is already on a call</h2>
        <p className="text-sm text-muted-foreground">
          The guest always gets a WhatsApp and you always get an alert in the app. Choose what happens next.
        </p>
      </div>

      <div className="space-y-2">
        <OptionCard
          checked={mode === "host_follows_up"}
          onCheckedChange={(on) => on && setMode("host_follows_up")}
          title="I'll follow up myself"
          description="Mira doesn't message them again. You get a ticket to call them back."
        />
        <OptionCard
          checked={mode === "callback_invite"}
          onCheckedChange={(on) => on && setMode("callback_invite")}
          title="Ask them to call back"
          description="When Mira is free, she messages the guest to ring again. If they don't, you get a ticket."
        >
          <div className="flex items-center gap-2">
            <Label htmlFor="callback-wait" className="text-sm font-normal text-muted-foreground">
              Wait
            </Label>
            <Input
              id="callback-wait"
              type="number"
              min={1}
              max={60}
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              className="w-20"
              aria-invalid={!validMinutes}
            />
            <span className="text-sm text-muted-foreground">minutes for them to ring back</span>
          </div>
        </OptionCard>
      </div>

      <Button onClick={save} disabled={!dirty || saving || (mode === "callback_invite" && !validMinutes)}>
        {saving ? "Saving…" : "Save"}
      </Button>
    </div>
  );
}
