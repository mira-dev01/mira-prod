"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/hooks/use-async";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useCapabilities } from "@/lib/capabilities-context";
import type { PropertyOut } from "@/lib/types";

type Group = { key: string; phone: string; propertyIds: string[] };

function groupsFrom(properties: PropertyOut[]): Group[] {
  const byPhone = new Map<string, string[]>();
  for (const p of properties) {
    if (p.host_transfer_phone) byPhone.set(p.host_transfer_phone, [...(byPhone.get(p.host_transfer_phone) ?? []), p.id]);
  }
  return [...byPhone].map(([phone, propertyIds]) => ({ key: phone, phone, propertyIds }));
}

/**
 * Where live transfers and host alerts (escalations, Take call, busy-call
 * and guest-reply WhatsApps) go. The account number always exists; with
 * "a number per group of properties" (Features & modules > Host handoff)
 * each group of properties can have its own, and properties left out use
 * the account number. Never changes which number guests dial.
 */
export function TransferNumbersCard() {
  const { user, refreshUser } = useAuth();
  const perProperty = user?.notification_preferences.transfer_number_mode === "per_property";

  return (
    <Card id="transfer-numbers">
      <CardHeader>
        <CardTitle>Host transfer number</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <AccountNumberForm
          initial={user?.phone ?? ""}
          label={perProperty ? "Main number" : "Your number"}
          help={
            perProperty
              ? "Used for any property without its own number below, and before a guest has picked a property."
              : "Live transfers, escalations and WhatsApp alerts for every property come to this number."
          }
          onSaved={refreshUser}
        />
        {perProperty && <PropertyGroupsEditor />}
      </CardContent>
    </Card>
  );
}

function AccountNumberForm({
  initial,
  label,
  help,
  onSaved,
}: {
  initial: string;
  label: string;
  help: string;
  onSaved: () => Promise<void>;
}) {
  const [phone, setPhone] = useState(initial);
  const [saving, setSaving] = useState(false);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.auth.updateMe({ phone: phone.trim() || null });
      await onSaved();
      toast.success("Transfer number saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't save the transfer number");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSave} className="space-y-1.5">
      <Label htmlFor="account-transfer-number">{label}</Label>
      <div className="flex gap-2">
        <Input
          id="account-transfer-number"
          placeholder="+9198XXXXXXXX"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
        <Button type="submit" disabled={saving}>
          Save
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{help}</p>
    </form>
  );
}

function PropertyGroupsEditor() {
  const { refetch: refetchCapabilities } = useCapabilities();
  const { data: properties, loading, error, refetch, setData } = useAsync(() => api.properties.list(), []);
  // Edits live in a draft until saved; until then the groups mirror what's stored.
  const [draft, setDraft] = useState<Group[] | null>(null);
  const [saving, setSaving] = useState(false);

  if (loading && !properties) return <Skeleton className="h-24 w-full" />;
  if (error || !properties) {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-muted-foreground">Couldn&apos;t load your properties.</p>
        <Button variant="outline" size="sm" onClick={refetch}>
          Try again
        </Button>
      </div>
    );
  }
  if (properties.length === 0) {
    return <p className="text-sm text-muted-foreground">Add a property first, then give it its own number here.</p>;
  }

  const groups = draft ?? groupsFrom(properties);
  const assigned = new Set(groups.flatMap((g) => g.propertyIds));
  const unassigned = properties.filter((p) => !assigned.has(p.id));

  function update(next: Group[]) {
    setDraft(next);
  }

  function toggleProperty(groupKey: string, propertyId: string, checked: boolean) {
    // A property belongs to one group: checking it here takes it out of any other.
    update(
      groups.map((g) => ({
        ...g,
        propertyIds:
          g.key === groupKey
            ? checked
              ? [...g.propertyIds, propertyId]
              : g.propertyIds.filter((id) => id !== propertyId)
            : g.propertyIds.filter((id) => id !== propertyId),
      }))
    );
  }

  async function handleSave() {
    const complete = groups.filter((g) => g.phone.trim() && g.propertyIds.length > 0);
    if (complete.length !== groups.filter((g) => g.phone.trim() || g.propertyIds.length > 0).length) {
      toast.error("Each number needs at least one property, and each property group needs a number.");
      return;
    }
    setSaving(true);
    try {
      const saved = await api.properties.setTransferNumbers(
        complete.map((g) => ({ phone: g.phone.trim(), property_ids: g.propertyIds }))
      );
      setData(saved);
      setDraft(null);
      void refetchCapabilities();
      toast.success("Transfer numbers saved");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't save transfer numbers");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3 border-t pt-4">
      <div>
        <p className="text-sm font-medium">Numbers per group of properties</p>
        <p className="text-xs text-muted-foreground">Tick the properties each number covers.</p>
      </div>
      {groups.map((group, index) => (
        <div key={group.key} className="space-y-2 rounded-lg border p-3">
          <div className="flex gap-2">
            <Input
              aria-label={`Number ${index + 1}`}
              placeholder="+9198XXXXXXXX"
              value={group.phone}
              onChange={(e) => update(groups.map((g) => (g.key === group.key ? { ...g, phone: e.target.value } : g)))}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Remove number ${index + 1}`}
              onClick={() => update(groups.filter((g) => g.key !== group.key))}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {properties.map((p) => (
              <label key={p.id} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={group.propertyIds.includes(p.id)}
                  onCheckedChange={(v) => toggleProperty(group.key, p.id, v === true)}
                />
                <span className="min-w-0 truncate">{p.name}</span>
              </label>
            ))}
          </div>
        </div>
      ))}
      {unassigned.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Using your main number: {unassigned.map((p) => p.name).join(", ")}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => update([...groups, { key: crypto.randomUUID(), phone: "", propertyIds: [] }])}
        >
          <Plus className="size-3.5" />
          Add another number
        </Button>
        <Button type="button" size="sm" onClick={handleSave} disabled={saving || draft === null}>
          {saving ? "Saving…" : "Save numbers"}
        </Button>
      </div>
    </div>
  );
}
