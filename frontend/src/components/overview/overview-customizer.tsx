"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { EyeOff, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ReorderableList } from "@/components/reorderable-list";
import { FALLBACK_WIDGETS } from "@/components/overview/widgets";
import { ApiError, api } from "@/lib/api";
import { cn, glassCardClassName } from "@/lib/utils";
import type { OverviewLayout, OverviewWidget, WidgetSize } from "@/lib/types";

/** The customizer's working copy. Only available widgets are editable;
 * the backend keeps unavailable widgets' saved settings untouched. */
export type OverviewDraft = { shown: OverviewWidget[]; hidden: OverviewWidget[] };

export function draftFromLayout(layout: OverviewLayout): OverviewDraft {
  const available = layout.widgets.filter((w) => w.available);
  return { shown: available.filter((w) => !w.hidden), hidden: available.filter((w) => w.hidden) };
}

const SIZE_LABEL: Record<WidgetSize, string> = { half: "Half", full: "Full" };

/**
 * Overview edit mode. The page renders the draft live underneath as the
 * preview; nothing is persisted until Save succeeds, and a failed save
 * keeps the draft. Sizes are limited to each widget's predefined options.
 */
export function OverviewCustomizer({
  layout,
  draft,
  onDraftChange,
  onSaved,
  onCancel,
  onReload,
}: {
  layout: OverviewLayout;
  draft: OverviewDraft;
  onDraftChange: (draft: OverviewDraft) => void;
  onSaved: (layout: OverviewLayout) => void;
  onCancel: () => void;
  onReload: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const unavailable = layout.widgets.filter((w) => !w.available);

  function setSize(id: string, size: WidgetSize) {
    onDraftChange({ ...draft, shown: draft.shown.map((w) => (w.id === id ? { ...w, size } : w)) });
  }

  function hide(widget: OverviewWidget) {
    onDraftChange({ shown: draft.shown.filter((w) => w.id !== widget.id), hidden: [...draft.hidden, widget] });
  }

  function add(widget: OverviewWidget) {
    onDraftChange({ shown: [...draft.shown, widget], hidden: draft.hidden.filter((w) => w.id !== widget.id) });
  }

  function recommended() {
    // The registry's recommended order, sizes and visibility -- limited to
    // widgets available right now. Still a draft until Save.
    const byId = new Map([...draft.shown, ...draft.hidden].map((w) => [w.id, w]));
    const defaults = FALLBACK_WIDGETS.filter((d) => byId.has(d.id)).map((d) => ({
      ...byId.get(d.id)!,
      size: d.size,
    }));
    onDraftChange({
      shown: defaults.filter((w) => FALLBACK_WIDGETS.find((d) => d.id === w.id)?.default_visible),
      hidden: defaults.filter((w) => !FALLBACK_WIDGETS.find((d) => d.id === w.id)?.default_visible),
    });
  }

  async function handleSave() {
    setSaving(true);
    try {
      const saved = await api.preferences.saveOverviewWidgets({
        widgets: [
          ...draft.shown.map((w) => ({ id: w.id, size: w.size, hidden: false })),
          ...draft.hidden.map((w) => ({ id: w.id, size: w.size, hidden: true })),
        ],
        expected_revision: layout.revision,
      });
      toast.success("Overview layout saved");
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
      const saved = await api.preferences.resetOverviewWidgets();
      toast.success("Overview reset to the recommended layout");
      onSaved(saved);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't reset your layout");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-label="Customize Overview" className={cn("space-y-4 rounded-2xl p-4", glassCardClassName)}>
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-sm font-medium">Customize Overview</h2>
          <p className="text-xs text-muted-foreground">
            Drag to reorder, pick a size, hide what you don&apos;t need. The preview below updates as you go. Hiding a
            widget never turns a feature off.
          </p>
        </div>
        <div className="flex shrink-0 gap-2 pt-2 sm:pt-0">
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
          <h3 className="text-xs font-medium text-muted-foreground">On your Overview</h3>
          {draft.shown.length === 0 ? (
            <p className="rounded-lg border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">
              No widgets shown. Add some from the list.
            </p>
          ) : (
            <ReorderableList
              items={draft.shown}
              onReorder={(shown) => onDraftChange({ ...draft, shown })}
              itemNoun="widget"
              itemLabel={(w) => w.name}
              renderItem={(widget) => (
                <div className="flex flex-wrap items-center gap-2 py-1 pr-1">
                  <span className="min-w-0 flex-1 truncate text-sm">{widget.name}</span>
                  {widget.sizes.length > 1 ? (
                    <div role="radiogroup" aria-label={`${widget.name} size`} className="flex rounded-md border p-0.5">
                      {widget.sizes.map((size) => (
                        <button
                          key={size}
                          type="button"
                          role="radio"
                          aria-checked={widget.size === size}
                          onClick={() => setSize(widget.id, size)}
                          className={cn(
                            "rounded px-2 py-0.5 text-xs transition-colors",
                            widget.size === size
                              ? "bg-accent font-medium text-accent-foreground"
                              : "text-muted-foreground hover:text-foreground"
                          )}
                        >
                          {SIZE_LABEL[size]}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">{SIZE_LABEL[widget.sizes[0]]} width</span>
                  )}
                  {widget.hideable && (
                    <button
                      type="button"
                      onClick={() => hide(widget)}
                      aria-label={`Hide ${widget.name}`}
                      title="Hide widget"
                      className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                      <EyeOff className="size-4" />
                    </button>
                  )}
                </div>
              )}
            />
          )}
        </div>

        <div className="space-y-2">
          <h3 className="text-xs font-medium text-muted-foreground">Add widgets</h3>
          {draft.hidden.length === 0 ? (
            <p className="text-xs text-muted-foreground">Every available widget is on your Overview.</p>
          ) : (
            <ul className="space-y-1">
              {draft.hidden.map((widget) => (
                <li key={widget.id} className="flex items-start gap-2 rounded-lg border bg-card px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm">{widget.name}</p>
                    {widget.description && <p className="text-xs text-muted-foreground">{widget.description}</p>}
                  </div>
                  <Button variant="outline" size="sm" onClick={() => add(widget)} aria-label={`Add ${widget.name}`}>
                    <Plus className="size-3.5" />
                    Add
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {unavailable.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {unavailable.map((w) => w.name).join(", ")} {unavailable.length === 1 ? "needs" : "need"} a feature
              that&apos;s off.{" "}
              <Link href="/dashboard/settings?tab=features" className="text-primary hover:underline">
                Features &amp; modules
              </Link>
            </p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-4 border-t pt-3 text-xs">
        <button type="button" className="text-muted-foreground hover:text-foreground" onClick={recommended}>
          Use recommended layout
        </button>
        {!layout.is_default && (
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground"
            onClick={handleResetSaved}
            disabled={saving}
          >
            Reset saved layout
          </button>
        )}
      </div>
    </section>
  );
}
