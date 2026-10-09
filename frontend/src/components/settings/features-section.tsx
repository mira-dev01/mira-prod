"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { CapabilityStateChip } from "@/components/capability-state-chip";
import { ApiError } from "@/lib/api";
import { useCapabilities } from "@/lib/capabilities-context";
import type { CapabilityStatus } from "@/lib/types";

/**
 * Settings > Features & modules. Every switch persists through
 * PATCH /capabilities/{id} (backend validates dependencies, ownership and
 * whether the change would contradict live behavior) -- nothing here is a
 * UI-only toggle. Reads and writes the dashboard's shared capability state
 * (lib/capabilities-context.tsx), so the sidebar, Overview and capability-
 * aware sections update immediately. "Always on" capabilities have no switch at all: they're
 * shared services or guest safety nets the rest of Mira relies on.
 */
export function FeaturesSection() {
  const { data, loading, error, refetch, update } = useCapabilities();
  const [savingId, setSavingId] = useState<string | null>(null);

  async function handleToggle(cap: CapabilityStatus, enabled: boolean) {
    setSavingId(cap.id);
    try {
      const next = await update(cap.id, { enabled });
      const updated = next.capabilities.find((c) => c.id === cap.id);
      toast.success(`${cap.name} turned ${enabled ? "on" : "off"}`, { description: updated?.effect || undefined });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : `Couldn't update ${cap.name}`);
    } finally {
      setSavingId(null);
    }
  }

  if (loading && !data) {
    return (
      <div className="space-y-4">
        {[0, 1, 2].map((i) => (
          <Card key={i}>
            <CardHeader>
              <Skeleton variant="text" className="w-1/3" />
            </CardHeader>
            <CardContent className="space-y-3">
              <Skeleton variant="text" />
              <Skeleton variant="text" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  // Never render a guessed feature state -- if it can't load, say so.
  if (error || !data) {
    return (
      <Card className="max-w-md">
        <CardContent className="space-y-3 py-6 text-sm">
          <p className="text-muted-foreground">Couldn&apos;t load your features.</p>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            Try again
          </Button>
        </CardContent>
      </Card>
    );
  }

  const enabledCount = data.capabilities.filter((c) => c.enabled).length;
  const needsSetup = data.capabilities.filter((c) => c.state === "needs_setup").length;

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        {enabledCount} of {data.capabilities.length} features on
        {needsSetup > 0 && ` · ${needsSetup} need setup`}. Turning a feature off keeps its data and settings, so you
        can turn it back on any time.
      </p>
      <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
        {data.groups.map((group) => {
          const items = data.capabilities.filter((c) => c.group === group.id);
          if (items.length === 0) return null;
          return (
            <Card key={group.id}>
              <CardHeader>
                <CardTitle>{group.name}</CardTitle>
                <p className="text-xs text-muted-foreground">{group.description}</p>
              </CardHeader>
              <CardContent className="divide-y p-0">
                {items.map((cap) => (
                  <CapabilityRow
                    key={cap.id}
                    cap={cap}
                    saving={savingId === cap.id}
                    disabled={savingId !== null}
                    onToggle={(v) => handleToggle(cap, v)}
                  />
                ))}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function CapabilityRow({
  cap,
  saving,
  disabled,
  onToggle,
}: {
  cap: CapabilityStatus;
  saving: boolean;
  disabled: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  const missing = cap.enabled ? cap.requirements.filter((r) => !r.met) : [];
  const toggleAllowed = cap.enabled ? cap.can_disable : cap.can_enable;
  const blockedReason = cap.enabled ? cap.disable_blocked_reason : cap.enable_blocked_reason;

  return (
    <div className="space-y-2 px-6 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{cap.name}</span>
            <CapabilityStateChip state={cap.state} />
          </div>
          <p className="text-xs text-muted-foreground">{cap.description}</p>
        </div>
        {cap.selectable ? (
          <Switch
            aria-label={`${cap.enabled ? "Turn off" : "Turn on"} ${cap.name}`}
            checked={cap.enabled}
            disabled={disabled || !toggleAllowed}
            onCheckedChange={(v) => onToggle(v === true)}
            className={saving ? "opacity-60" : undefined}
          />
        ) : (
          <Badge variant="outline" className="shrink-0">
            Always on
          </Badge>
        )}
      </div>

      {cap.effect && <p className="text-xs text-foreground/80">{cap.effect}</p>}

      {cap.selectable && !toggleAllowed && blockedReason && (
        <p className="text-xs text-muted-foreground">{blockedReason}</p>
      )}

      {missing.length > 0 && (
        <ul className="space-y-1">
          {missing.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-2 text-xs">
              <span className="text-muted-foreground">
                {r.hard ? "Needed" : "Recommended"}: {r.label}
              </span>
              <Link
                href={r.action_route}
                className="flex shrink-0 items-center gap-1 font-medium text-primary hover:underline"
              >
                {r.action_label}
                <ArrowRight className="size-3" />
              </Link>
            </li>
          ))}
        </ul>
      )}

      {cap.integrations.some((i) => !i.connected) && (
        <p className="text-xs text-muted-foreground">
          {cap.integrations
            .filter((i) => !i.connected)
            .map((i) => `${i.label} not connected${i.required ? "" : " — falls back gracefully"}`)
            .join(" · ")}
        </p>
      )}
    </div>
  );
}
