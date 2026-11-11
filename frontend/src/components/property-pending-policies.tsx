"use client";

import { useState } from "react";
import { Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { PropertyFormSection } from "@/components/property-form-section";
import { api, ApiError } from "@/lib/api";
import type { PolicyOut, PolicyOverview } from "@/lib/types";

const OTHER = "other";

/** The policies waiting on the host for one property, as the server worked them out. */
export function pendingPolicies(overview: PolicyOverview, propertyId: string): PolicyOut[] {
  const ids = new Set(overview.properties.find((p) => p.id === propertyId)?.pending_policy_ids ?? []);
  return [...overview.rows.flatMap((row) => row.items), ...overview.drafts].filter((p) => ids.has(p.id));
}

/** How many policies are waiting for this property (0 when the policies have not loaded). */
export function pendingCount(overview: PolicyOverview | null, propertyId: string): number {
  return overview?.properties.find((p) => p.id === propertyId)?.pending_policy_ids.length ?? 0;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function IconButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className="rounded p-1 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
    >
      {children}
    </button>
  );
}

function PendingEntry({
  policy,
  propertyId,
  propertyName,
  topicLabel,
  onChanged,
}: {
  policy: PolicyOut;
  propertyId: string;
  propertyName: string;
  topicLabel: string;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [text, setText] = useState(policy.text);
  const [busy, setBusy] = useState(false);
  // One policy can name several properties: editing its wording changes it for each of them.
  const shared = policy.property_id === null;
  const where =
    policy.property_id === propertyId
      ? "Draft for this property"
      : policy.status === "draft"
        ? "Draft that names this property"
        : "Names this property, which it could not be matched to before";

  async function approve() {
    setBusy(true);
    try {
      await api.policies.assign(policy.id, propertyId);
      toast.success(`Approved for ${propertyName}. Mira will use it on her next call.`);
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't approve"));
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    try {
      await api.policies.update(policy.id, { text });
      toast.success("Saved. It still needs your approval.");
      setEditing(false);
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't save"));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.policies.dismiss(policy.id, propertyId);
      toast.success(`Removed for ${propertyName}.`);
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't remove"));
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <li className="space-y-2 border-b border-border py-4 last:border-b-0">
        <p className="text-xs text-muted-foreground">{topicLabel}</p>
        <Textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} maxLength={800} aria-label="Policy" />
        {shared && (
          <p className="text-xs text-muted-foreground">
            This wording is shared with the other properties it names, so the change applies to each of them.
          </p>
        )}
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={save} disabled={busy || !text.trim() || text.trim() === policy.text}>
            Save
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setText(policy.text);
              setEditing(false);
            }}
            disabled={busy}
          >
            Cancel
          </Button>
        </div>
      </li>
    );
  }

  return (
    <li className="flex items-start gap-3 border-b border-border py-4 last:border-b-0">
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-xs text-muted-foreground">
          {topicLabel} · {where}
        </p>
        <p className="text-sm">{policy.text}</p>
        {policy.review_notes.map((note) => (
          <p key={note} className="text-xs" style={{ color: "var(--status-pending-strong)" }}>
            {note}
          </p>
        ))}
        <div className="flex items-center gap-2 pt-2">
          <Button size="sm" onClick={approve} disabled={busy || confirming}>
            Approve for this property
          </Button>
        </div>
      </div>
      {confirming ? (
        <div className="flex shrink-0 items-center gap-2 pt-0.5 text-xs">
          <span className="text-muted-foreground">Remove for this property?</span>
          <button type="button" className="font-medium text-destructive hover:underline" onClick={remove} disabled={busy}>
            Delete
          </button>
          <button
            type="button"
            className="text-muted-foreground hover:underline"
            onClick={() => setConfirming(false)}
            disabled={busy}
          >
            Keep
          </button>
        </div>
      ) : (
        <div className="flex shrink-0 items-center">
          <IconButton label="Edit" onClick={() => setEditing(true)} disabled={busy}>
            <Pencil className="size-3.5" />
          </IconButton>
          <IconButton label="Delete for this property" onClick={() => setConfirming(true)} disabled={busy}>
            <Trash2 className="size-3.5" />
          </IconButton>
        </div>
      )}
    </li>
  );
}

/**
 * The property pane's "waiting for you" block: policies that name this property but have not been
 * approved for it yet (a draft read from a document, or one that named it before it existed). Approve
 * makes it this property's own policy; Delete drops it for this property only. Nothing here is spoken
 * to guests until it is approved. Renders nothing when there is nothing waiting.
 */
export function PropertyPendingPolicies({
  overview,
  propertyId,
  propertyName,
  onChanged,
}: {
  overview: PolicyOverview | null;
  propertyId: string;
  propertyName: string;
  onChanged: () => void;
}) {
  if (!overview) return null;
  const items = pendingPolicies(overview, propertyId);
  if (items.length === 0) return null;
  const label = (p: PolicyOut) =>
    p.topic === OTHER ? p.title : (overview.topics.find((t) => t.key === p.topic)?.label ?? p.topic);

  return (
    <PropertyFormSection
      title="Policies waiting for you"
      count={items.length}
      helpText="Mira does not use these until you approve them for this property."
    >
      <ul className="px-5">
        {items.map((policy) => (
          <PendingEntry
            key={policy.id}
            policy={policy}
            propertyId={propertyId}
            propertyName={propertyName}
            topicLabel={label(policy)}
            onChanged={onChanged}
          />
        ))}
      </ul>
    </PropertyFormSection>
  );
}
