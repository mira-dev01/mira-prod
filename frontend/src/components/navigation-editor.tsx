"use client";

import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Eye, EyeOff, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ReorderableList } from "@/components/reorderable-list";
import { ApiError } from "@/lib/api";
import { FALLBACK_NAV, NavIcon } from "@/lib/navigation";
import { useNavigation } from "@/lib/navigation-context";
import { cn } from "@/lib/utils";
import type { NavigationPreferences, NavItem } from "@/lib/types";

/**
 * Sidebar edit mode. Works on a local draft; nothing changes for real
 * until Save succeeds, and a failed save keeps the draft so the host never
 * loses their edits. Overview and Settings are pinned; Properties can move
 * but never hide. Only destinations available under the host's current
 * capabilities can be arranged -- the backend re-validates all of this.
 * Mount with key={prefs.revision} so reloading the latest layout resets it.
 */
export function NavigationEditor({ prefs, onClose }: { prefs: NavigationPreferences; onClose: () => void }) {
  const { save, reset, refetch } = useNavigation();
  const pinnedTop = prefs.items.filter((i) => i.placement === "pinned_top");
  const pinnedBottom = prefs.items.filter((i) => i.placement === "pinned_bottom");
  const unavailable = prefs.items.filter((i) => !i.available);

  const [order, setOrder] = useState<NavItem[]>(() =>
    prefs.items.filter((i) => i.placement === "movable" && i.available)
  );
  const [hidden, setHidden] = useState<Set<string>>(
    () => new Set(prefs.items.filter((i) => i.hidden && i.available).map((i) => i.id))
  );
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);

  function toggleHidden(id: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function resetDraftToDefault() {
    // Default order = the registry's (mirrored by FALLBACK_NAV); nothing
    // hidden. Still a draft -- Save persists it.
    const rank = new Map(FALLBACK_NAV.map((item, index) => [item.id, index]));
    setOrder((prev) => [...prev].sort((a, b) => (rank.get(a.id) ?? 99) - (rank.get(b.id) ?? 99)));
    setHidden(new Set());
  }

  async function handleSave() {
    setSaving(true);
    try {
      await save({
        order: order.map((i) => i.id),
        hidden: [...hidden],
        expected_revision: prefs.revision,
      });
      toast.success("Navigation saved");
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setConflict(true);
      toast.error(err instanceof ApiError ? err.message : "Couldn't save your navigation — your changes are still here.");
    } finally {
      setSaving(false);
    }
  }

  async function handleResetSaved() {
    setSaving(true);
    try {
      await reset();
      toast.success("Navigation reset to default");
      onClose();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Couldn't reset your navigation");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-2 overflow-y-auto" aria-label="Customize navigation" role="region">
      <div className="px-2 pt-2">
        <p className="text-sm font-medium">Customize navigation</p>
        <p className="text-xs text-muted-foreground">
          Drag to reorder. Hiding a page only removes it from this menu — the feature keeps working.
        </p>
      </div>

      {pinnedTop.map((item) => (
        <PinnedRow key={item.id} item={item} />
      ))}

      <ReorderableList
        items={order}
        onReorder={setOrder}
        itemNoun="navigation item"
        itemLabel={(i) => i.label}
        renderItem={(item) => {
          const isHidden = hidden.has(item.id);
          return (
            <div className="flex items-center gap-2">
              <NavIcon id={item.id} className={cn("size-4 shrink-0", isHidden && "opacity-40")} />
              <span className={cn("min-w-0 flex-1 truncate text-sm", isHidden && "text-muted-foreground line-through")}>
                {item.label}
              </span>
              {item.hideable ? (
                <button
                  type="button"
                  onClick={() => toggleHidden(item.id)}
                  aria-pressed={isHidden}
                  aria-label={isHidden ? `Show ${item.label}` : `Hide ${item.label}`}
                  title={isHidden ? "Show in menu" : "Hide from menu"}
                  className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
                >
                  {isHidden ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              ) : (
                <span className="flex size-7 shrink-0 items-center justify-center" title="Always shown">
                  <Lock className="size-3.5 text-muted-foreground" aria-label="Always shown" />
                </span>
              )}
            </div>
          );
        }}
      />

      {pinnedBottom.map((item) => (
        <PinnedRow key={item.id} item={item} />
      ))}

      {unavailable.length > 0 && (
        <p className="px-2 text-xs text-muted-foreground">
          {unavailable.map((i) => i.label).join(", ")} {unavailable.length === 1 ? "isn't" : "aren't"} available
          with your current features.{" "}
          <Link href="/dashboard/settings?tab=features" className="text-primary hover:underline" onClick={onClose}>
            Features &amp; modules
          </Link>
        </p>
      )}

      {conflict && (
        <div className="space-y-1 rounded-md border px-2 py-2 text-xs">
          <p className="text-muted-foreground">This menu was changed in another tab or device.</p>
          <button type="button" className="font-medium text-primary hover:underline" onClick={() => void refetch()}>
            Load the latest version
          </button>
        </div>
      )}

      <div className="mt-auto space-y-2 pt-2">
        <div className="flex gap-2">
          <Button variant="outline" size="sm" className="min-w-0 flex-1" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button size="sm" className="min-w-0 flex-1" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
        <div className="flex justify-between gap-2 px-1 text-xs">
          <button type="button" className="text-muted-foreground hover:text-foreground" onClick={resetDraftToDefault}>
            Default order
          </button>
          {!prefs.is_default && (
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
      </div>
    </div>
  );
}

function PinnedRow({ item }: { item: NavItem }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground">
      <NavIcon id={item.id} className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      <Lock className="size-3.5" aria-label="Pinned" />
    </div>
  );
}
