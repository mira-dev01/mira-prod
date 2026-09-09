"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { TIMEZONES, formatHourMinute, timezoneLabel } from "@/lib/timezones";

// Account-global host call hours -- one window, common to every property on
// the account, during which inbound guest calls are routed to the host's
// phone instead of Mira. Replaces the former per-property "Call ownership"
// card (components/settings/call-ownership-card.tsx). See
// documentation/host-call-hours-and-handoff.md.
//
// NOTE: while backend/render.yaml's FIXED_HOST_HOURS_* env override is
// active, every host is forced onto one global 11:00-17:00 IST window and
// what's saved here has no effect until that override is removed. This
// card's Save still works; the override is a temporary rollout measure.

export function HostCallHoursCard() {
  const { user, refreshUser } = useAuth();

  const [enabled, setEnabled] = useState(user?.host_call_hours_enabled ?? false);
  const [start, setStart] = useState(user?.host_call_hours_start ?? "");
  const [end, setEnd] = useState(user?.host_call_hours_end ?? "");
  const [timezone, setTimezone] = useState(user?.host_call_hours_timezone ?? "Asia/Kolkata");
  const [saving, setSaving] = useState(false);

  // Resync local state whenever the underlying user data changes (e.g.
  // after a save/refetch elsewhere). Same pattern as the other Settings
  // cards' local form state.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- resyncing local edits to refreshed server data is the point of this effect
    setEnabled(user?.host_call_hours_enabled ?? false);
    setStart(user?.host_call_hours_start ?? "");
    setEnd(user?.host_call_hours_end ?? "");
    setTimezone(user?.host_call_hours_timezone ?? "Asia/Kolkata");
  }, [user]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (enabled && (!start || !end)) {
      toast.error("Enter both a start and end time");
      return;
    }
    setSaving(true);
    try {
      await api.auth.updateMe(
        enabled
          ? {
              host_call_hours_enabled: true,
              host_call_hours_start: start,
              host_call_hours_end: end,
              host_call_hours_timezone: timezone,
            }
          : { host_call_hours_enabled: false }
      );
      await refreshUser();
      toast.success("Host call hours saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save host call hours");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Host call hours</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          During these hours, guest calls go to your phone instead of Mira. Outside them, Mira answers.
          Applies to every property on your account.
        </p>

        <form onSubmit={handleSave} className="space-y-4">
          <div className="flex items-center gap-3">
            <Switch id="host-call-hours-enabled" checked={enabled} onCheckedChange={setEnabled} />
            <Label htmlFor="host-call-hours-enabled">Send calls to my phone during set hours</Label>
          </div>

          {enabled && (
            <div className="space-y-4 rounded-lg border bg-muted/50 p-4">
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="space-y-2">
                  <Label htmlFor="host-call-hours-start">You answer from</Label>
                  <Input
                    id="host-call-hours-start"
                    type="time"
                    required
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="host-call-hours-end">You answer until</Label>
                  <Input
                    id="host-call-hours-end"
                    type="time"
                    required
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="host-call-hours-timezone">Timezone</Label>
                  <Select value={timezone} onValueChange={(v) => v && setTimezone(v)}>
                    <SelectTrigger id="host-call-hours-timezone" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TIMEZONES.map((tz) => (
                        <SelectItem key={tz.value} value={tz.value}>
                          {tz.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <p className="text-xs text-muted-foreground">
                These hours apply every day. An overnight window (e.g. 10:00 PM – 6:00 AM) is fine — it
                stays in effect across midnight.
              </p>

              {start && end ? (
                <div className="rounded-md border border-dashed bg-background p-3 text-sm">
                  <p>
                    Calls go to you <span className="font-medium">{formatHourMinute(start)}</span>
                    {" – "}
                    <span className="font-medium">{formatHourMinute(end)}</span> ({timezoneLabel(timezone)}).
                  </p>
                  <p className="text-muted-foreground">Outside these hours, Mira answers calls.</p>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">Enter both times to see a preview.</p>
              )}
            </div>
          )}

          <Button type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
