"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { EyeOff, Heading, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ReorderableList } from "@/components/reorderable-list";
import { SizeChoice } from "@/components/overview/overview-customizer";
import { ApiError, api } from "@/lib/api";
import type { AnalyticsEntry, AnalyticsLayout, AnalyticsSize } from "@/lib/types";

/** Shown entries (metrics + headings, in order) and hidden metrics. Only
 * available metrics are editable; the backend keeps the rest untouched. */
export type AnalyticsDraft = { shown: AnalyticsEntry[]; hidden: AnalyticsEntry[] };

export function analyticsDraftFrom(layout: AnalyticsLayout): AnalyticsDraft {
  const editable = layout.entries.filter((e) => e.type === "heading" || e.available);
  return {
    shown: editable.filter((e) => e.type === "heading" || !e.hidden),
    hidden: editable.filter((e) => e.type === "metric" && e.hidden),
  };
}

const SIZE_LABEL: Record<AnalyticsSize, string> = { sm: "Small", md: "Wide", half: "Half", full: "Full" };

function newHeading(): AnalyticsEntry {
  return {
    type: "heading",
    id: `h-${crypto.randomUUID().slice(0, 8)}`,
    title: "New section",
    subtitle: null,
    label: null,
    description: null,
    formula: null,
    kind: null,
    sizes: [],
    size: null,
    hidden: false,
    available: true,
    unavailable_reason: null,
  };
}

/**
 * Analytics edit mode: reorder metric tiles, panels and your own headings;
 * resize within each metric's predefined sizes; hide or add metrics; add,
 * rename or delete headings. The page underneath renders the draft live as
 * the preview. Nothing persists until Save succeeds, and a failed save keeps
 * the draft.
 */
export function AnalyticsCustomizer({
  layout,
  draft,
  onDraftChange,
  onSaved,
  onCancel,
  onReload,
}: {
  layout: AnalyticsLayout;
  draft: AnalyticsDraft;
  onDraftChange: (draft: AnalyticsDraft) => void;
  onSaved: (layout: AnalyticsLayout) => void;
  onCancel: () => void;
  onReload: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const unavailable = layout.entries.filter((e) => e.type === "metric" && !e.available);

  function patch(id: string, change: Partial<AnalyticsEntry>) {
    onDraftChange({ ...draft, shown: draft.shown.map((e) => (e.id === id ? { ...e, ...change } : e)) });
  }

  function remove(entry: AnalyticsEntry) {
    onDraftChange({
      shown: draft.shown.filter((e) => e.id !== entry.id),
      // Headings are deleted outright; metrics go back to "Add metrics".
      hidden: entry.type === "metric" ? [...draft.hidden, entry] : draft.hidden,
    });
  }

  function add(entry: AnalyticsEntry) {
    onDraftChange({ shown: [...draft.shown, entry], hidden: draft.hidden.filter((e) => e.id !== entry.id) });
  }

  async function handleSave() {
    if (draft.shown.some((e) => e.type === "heading" && !(e.title ?? "").trim())) {
      toast.error("Every heading needs a title.");
      return;
    }
    setSaving(true);
    try {
      const saved = await api.preferences.saveAnalyticsWidgets({
        entries: [
          ...draft.shown.map((e) =>
            e.type === "heading"
              ? { type: "heading" as const, id: e.id, title: e.title, subtitle: e.subtitle || null }
              : { type: "metric" as const, id: e.id, size: e.size, hidden: false }
          ),
          ...draft.hidden.map((e) => ({ type: "metric" as const, id: e.id, size: e.size, hidden: true })),
        ],
        expected_revision: layout.revision,
      });
      toast.success("Analytics layout saved");
      onSaved(saved);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setConflict(true);
      toast.error(err instanceof ApiError ? err.message : "Couldn't save your layout — your changes are still here.");
    } finally {
      setSaving(false);
    }
  }

  async function handleResetSaved() {
    setSaving(true);
    try {
      const saved = await api.preferences.resetAnalyticsWidgets();
      toast.success("Analytics reset to the standard layout");
      onSaved(saved);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't reset your layout");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card aria-label="Customize Analytics" role="region">
      <CardContent className="space-y-4 py-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-sm font-medium">Customize Analytics</h2>
            <p className="text-xs text-muted-foreground">
              Drag to reorder, pick sizes, hide metrics and write your own headings. The page below updates as you
              go. Hiding a metric never changes how anything is calculated.
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="outline" size="sm" onClick={onCancel} disabled={saving}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save layout"}
            </Button>
          </div>
        </div>

        {conflict && (
          <p className="rounded-md border px-3 py-2 text-xs text-muted-foreground">
            This layout was changed in another tab or device.{" "}
            <button type="button" className="font-medium text-primary hover:underline" onClick={onReload}>
              Load the latest version
            </button>
          </p>
        )}

        <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
          <div className="space-y-2">
            <h3 className="text-xs font-medium text-muted-foreground">On your page</h3>
            <ReorderableList
              items={draft.shown}
              onReorder={(shown) => onDraftChange({ ...draft, shown })}
              itemNoun="analytics item"
              itemLabel={(e) => (e.type === "heading" ? `Heading ${e.title ?? ""}` : (e.label ?? e.id))}
              renderItem={(entry) =>
                entry.type === "heading" ? (
                  <div className="flex items-start gap-2 py-1 pr-1">
                    <Heading className="mt-2 size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1 space-y-1">
                      <Input
                        aria-label="Heading title"
                        maxLength={80}
                        value={entry.title ?? ""}
                        onChange={(e) => patch(entry.id, { title: e.target.value })}
                      />
                      <Input
                        aria-label="Heading subtitle"
                        maxLength={160}
                        placeholder="Subtitle (optional)"
                        value={entry.subtitle ?? ""}
                        onChange={(e) => patch(entry.id, { subtitle: e.target.value })}
                      />
                    </div>
                    <button
                      type="button"
                      onClick={() => remove(entry)}
                      aria-label={`Delete heading ${entry.title ?? ""}`}
                      className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-2 py-1 pr-1">
                    <span className="min-w-0 flex-1 truncate text-sm">{entry.label}</span>
                    <SizeChoice
                      label={`${entry.label} size`}
                      sizes={entry.sizes}
                      value={entry.size ?? entry.sizes[0]}
                      labels={SIZE_LABEL}
                      onChange={(size) => patch(entry.id, { size })}
                    />
                    <button
                      type="button"
                      onClick={() => remove(entry)}
                      aria-label={`Hide ${entry.label}`}
                      title="Hide metric"
                      className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                      <EyeOff className="size-4" />
                    </button>
                  </div>
                )
              }
            />
            <Button type="button" variant="outline" size="sm" onClick={() => add(newHeading())}>
              <Plus className="size-3.5" />
              Add heading
            </Button>
          </div>

          <div className="space-y-2">
            <h3 className="text-xs font-medium text-muted-foreground">Add metrics</h3>
            {draft.hidden.length === 0 ? (
              <p className="text-xs text-muted-foreground">Every available metric is on your page.</p>
            ) : (
              <ul className="space-y-1">
                {draft.hidden.map((entry) => (
                  <li key={entry.id} className="flex items-start gap-2 rounded-lg border bg-card px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm">{entry.label}</p>
                      {entry.description && <p className="text-xs text-muted-foreground">{entry.description}</p>}
                    </div>
                    <Button variant="outline" size="sm" onClick={() => add(entry)} aria-label={`Add ${entry.label}`}>
                      <Plus className="size-3.5" />
                      Add
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            {unavailable.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {unavailable.map((e) => e.label).join(", ")} {unavailable.length === 1 ? "needs" : "need"} a feature
                that&apos;s off.{" "}
                <Link href="/dashboard/settings?tab=features" className="text-primary hover:underline">
                  Features &amp; modules
                </Link>
              </p>
            )}
          </div>
        </div>

        {!layout.is_default && (
          <div className="border-t pt-3 text-xs">
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground"
              onClick={handleResetSaved}
              disabled={saving}
            >
              Reset to the standard layout
            </button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
