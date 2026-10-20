"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2, Phone, PhoneOff } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { CallHoursStatus } from "@/lib/types";
import { TIMEZONES, formatHourMinute, timezoneLabel } from "@/lib/timezones";

// Account-global host call hours -- one window, common to every property on
// the account, during which inbound guest calls are routed to the host's
// phone instead of Mira. Replaces the former per-property "Call ownership"
// card (components/settings/call-ownership-card.tsx). See
// documentation/host-call-hours-and-handoff.md.
//
// This is the sole routing input as of the FIXED_HOST_HOURS_* env-var
// override's removal -- what's saved here takes effect immediately, with
// no other setting able to override or shadow it.

export function HostCallHoursCard() {
  const { user, setUserData } = useAuth();

  const [enabled, setEnabled] = useState(user?.host_call_hours_enabled ?? false);
  const [start, setStart] = useState(user?.host_call_hours_start ?? "");
  const [end, setEnd] = useState(user?.host_call_hours_end ?? "");
  const [timezone, setTimezone] = useState(user?.host_call_hours_timezone ?? "Asia/Kolkata");
  const [saving, setSaving] = useState(false);

  // Live confirmation of what's actually in effect right now, computed
  // server-side by the exact same resolver the Exotel webhook calls on a
  // real inbound call (see GET /auth/me/call-hours-status) -- this can
  // never drift from what the next real call will do, unlike a purely
  // client-side "is now between start and end" calculation, which would
  // silently go stale the moment the resolver's own logic changes.
  const [liveStatus, setLiveStatus] = useState<CallHoursStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  const fetchStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      setLiveStatus(await api.auth.callHoursStatus());
    } catch {
      // Silent -- this indicator is a confirmation nicety, not load-bearing;
      // the form above still reflects the saved config either way.
      setLiveStatus(null);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

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
      const updated = await api.auth.updateMe(
        enabled
          ? {
              host_call_hours_enabled: true,
              host_call_hours_start: start,
              host_call_hours_end: end,
              host_call_hours_timezone: timezone,
            }
          : { host_call_hours_enabled: false }
      );
      // Apply the PATCH's own response directly rather than issuing a
      // separate refreshUser() GET -- avoids a second round-trip racing
      // against any other in-flight /auth/me fetch (setUserData still goes
      // through auth-context's sequence guard, so this can't itself be
      // clobbered by an older one either).
      setUserData(updated);
      toast.success("Host call hours saved");
      // The save above is what the next real call will see immediately
      // (no deploy/propagation delay -- it's a DB write, read fresh on
      // every inbound call), but re-check the live resolver anyway so the
      // status line reflects the just-saved window/timezone instead of
      // whatever it showed before this save.
      await fetchStatus();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save host call hours");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="lg:col-span-2">
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle>Host call hours</CardTitle>
        <StatusBadge loading={statusLoading} status={liveStatus} />
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

function StatusBadge({ loading, status }: { loading: boolean; status: CallHoursStatus | null }) {
  if (loading) {
    return (
      <Badge variant="outline" className="gap-1.5 text-muted-foreground">
        <Loader2 className="size-3 animate-spin" />
        Checking…
      </Badge>
    );
  }

  if (status === null) {
    // The save itself still succeeded (or failed with its own toast) --
    // this only means the confirmation check couldn't be reached, so it
    // says that plainly rather than guessing at HOST/MIRA.
    return (
      <Badge variant="outline" className="text-muted-foreground">
        Status unavailable
      </Badge>
    );
  }

  if (status.current_owner === "HOST") {
    return (
      <Badge className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-600">
        <Phone className="size-3" />
        Live: calls ring your phone
      </Badge>
    );
  }

  return (
    <Badge variant="secondary" className="gap-1.5">
      <PhoneOff className="size-3" />
      Live: Mira is answering
    </Badge>
  );
}
