"use client";

import { useRef, useState } from "react";
import { ChevronDown, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PolicyOut, PolicyOverview, PolicyRow, PolicyRowState } from "@/lib/types";

const ACCEPT = ".pdf,.docx,.xlsx,.csv,.txt,.md,.png,.jpg,.jpeg,.webp";
const OTHER = "other";

// One colour language for the whole tab; the legend on top explains it.
const DOT: Record<PolicyRowState, { color: string; label: string }> = {
  everyone: { color: "var(--status-live)", label: "All properties follow this" },
  some_differ: { color: "var(--status-pending)", label: "Some properties differ" },
  property_only: { color: "var(--status-progress)", label: "Set for specific properties only" },
  unset: { color: "transparent", label: "Not set" },
};

function Dot({ state, className }: { state: PolicyRowState; className?: string }) {
  const { color } = DOT[state];
  return (
    <span
      aria-hidden
      className={cn("inline-block size-2.5 shrink-0 rounded-full border", className)}
      style={{ backgroundColor: color, borderColor: state === "unset" ? "var(--muted-foreground)" : color }}
    />
  );
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function Legend() {
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-muted-foreground" aria-label="What the dots mean">
      {(Object.keys(DOT) as PolicyRowState[]).map((state) => (
        <li key={state} className="flex items-center gap-1.5">
          <Dot state={state} />
          {DOT[state].label}
        </li>
      ))}
    </ul>
  );
}

function summaryLine(row: PolicyRow, propertyCount: number): string {
  const names = row.items.filter((i) => i.property_id).map((i) => i.property_name ?? "a property");
  if (row.state === "everyone") return propertyCount > 1 ? `All ${propertyCount} properties follow this` : "Applies to your property";
  if (row.state === "some_differ") return `${row.override_count} of ${propertyCount} differ: ${names.join(", ")}`;
  if (row.state === "property_only") return `Only ${names.join(", ")}`;
  return "Not set";
}

/** Edit or remove one live policy. */
function ItemEditor({
  item,
  showTitle,
  removeLabel = "Remove",
  onChanged,
}: {
  item: PolicyOut;
  showTitle?: boolean;
  removeLabel?: string;
  onChanged: () => void;
}) {
  const [text, setText] = useState(item.text);
  const [title, setTitle] = useState(item.title);
  const [busy, setBusy] = useState(false);
  const dirty = text.trim() !== item.text || (showTitle && title.trim() !== item.title);

  async function save() {
    setBusy(true);
    try {
      await api.policies.update(item.id, { text, ...(showTitle ? { title } : {}) });
      toast.success("Saved. Mira will use this on her next call.");
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
      await api.policies.remove(item.id);
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't remove"));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {showTitle && <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Short title" maxLength={120} />}
      <Textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={800} aria-label="Policy" />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={busy || !dirty || !text.trim()}>
          Save
        </Button>
        <Button size="sm" variant="ghost" onClick={remove} disabled={busy}>
          {removeLabel}
        </Button>
      </div>
    </div>
  );
}

/** Write a new live policy for the host or for one property. */
function NewPolicy({
  topic,
  propertyId,
  showTitle,
  label,
  onChanged,
}: {
  topic: string;
  propertyId: string | null;
  showTitle?: boolean;
  label: string;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open)
    return (
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        {label}
      </Button>
    );

  async function create() {
    setBusy(true);
    try {
      await api.policies.create({ topic, text, property_id: propertyId, ...(showTitle ? { title } : {}) });
      toast.success("Saved. Mira will use this on her next call.");
      setOpen(false);
      setText("");
      setTitle("");
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't save"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {showTitle && <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Short title" maxLength={120} />}
      <Textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} maxLength={800} placeholder="Write it the way a guest should hear it" aria-label="Policy" />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={create} disabled={busy || !text.trim() || (showTitle && !title.trim())}>
          Save
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function TopicRow({ row, overview, onChanged }: { row: PolicyRow; overview: PolicyOverview; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const isOther = row.topic === OTHER;
  const hostWide = row.items.find((i) => !i.property_id);
  const own = new Map(row.items.filter((i) => i.property_id).map((i) => [i.property_id as string, i]));
  const propertyCount = overview.properties.length;

  return (
    <li className="py-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 text-left"
      >
        <Dot state={row.state} className="mt-1.5" />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">{row.label}</span>
          <span className="block text-xs text-muted-foreground">{summaryLine(row, propertyCount)}</span>
          {!open && hostWide && !isOther && <span className="mt-1 block truncate text-sm text-muted-foreground">{hostWide.text}</span>}
        </span>
        <ChevronDown className={cn("mt-1 size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="mt-3 space-y-4 pl-[22px]">
          <p className="text-xs text-muted-foreground">{row.hint}</p>

          {isOther ? (
            <>
              {row.items.map((item) => (
                <div key={item.id} className="space-y-1">
                  <p className="text-xs text-muted-foreground">{item.property_name ? `Only ${item.property_name}` : "All properties"}</p>
                  <ItemEditor item={item} showTitle onChanged={onChanged} />
                </div>
              ))}
              <NewPolicy topic={OTHER} propertyId={null} showTitle label="Add a note for all properties" onChanged={onChanged} />
            </>
          ) : (
            <>
              <div className="space-y-1">
                <p className="text-xs font-medium">{propertyCount > 1 ? "All properties" : "Policy"}</p>
                {hostWide ? (
                  <ItemEditor item={hostWide} onChanged={onChanged} />
                ) : (
                  <NewPolicy topic={row.topic} propertyId={null} label={propertyCount > 1 ? "Set for all properties" : "Add policy"} onChanged={onChanged} />
                )}
              </div>

              {propertyCount > 1 && (
                <div className="space-y-3 border-t pt-3">
                  <p className="text-xs font-medium">By property</p>
                  {overview.properties.map((property) => {
                    const item = own.get(property.id);
                    const state: PolicyRowState = item ? "some_differ" : hostWide ? "everyone" : "unset";
                    return (
                      <div key={property.id} className="space-y-2">
                        <div className="flex items-center gap-2 text-sm">
                          <Dot state={state} />
                          <span className="font-medium">{property.name}</span>
                          <span className="text-xs text-muted-foreground">
                            {item ? "Has its own policy" : hostWide ? "Follows the policy for all properties" : "Not set"}
                          </span>
                        </div>
                        <div className="pl-[18px]">
                          {item ? (
                            <ItemEditor item={item} removeLabel="Use the policy for all properties" onChanged={onChanged} />
                          ) : (
                            <NewPolicy
                              topic={row.topic}
                              propertyId={property.id}
                              label={hostWide ? "Make it different here" : "Set for this property"}
                              onChanged={onChanged}
                            />
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

/** A policy read from a document, waiting for the host to check it. */
function DraftCard({ draft, overview, onChanged }: { draft: PolicyOut; overview: PolicyOverview; onChanged: () => void }) {
  const [text, setText] = useState(draft.text);
  const [topic, setTopic] = useState(draft.topic);
  const [busy, setBusy] = useState(false);
  const label = overview.topics.find((t) => t.key === topic)?.label ?? topic;

  const row = overview.rows.find((r) => r.topic === topic);
  const replaces = topic !== OTHER && row?.items.some((i) => i.property_id === draft.property_id);

  async function approve() {
    setBusy(true);
    try {
      if (text.trim() !== draft.text || topic !== draft.topic) await api.policies.update(draft.id, { text, topic });
      await api.policies.approve(draft.id);
      toast.success("Approved. Mira will use this on her next call.");
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't approve"));
      setBusy(false);
    }
  }

  async function discard() {
    setBusy(true);
    try {
      await api.policies.remove(draft.id);
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't discard"));
      setBusy(false);
    }
  }

  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={topic} onValueChange={(v) => v && setTopic(v)}>
          <SelectTrigger className="w-56">
            <SelectValue>{label}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {overview.topics.map((t) => (
              <SelectItem key={t.key} value={t.key}>
                {t.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">{draft.property_name ? `Only ${draft.property_name}` : "All properties"}</span>
      </div>
      <Textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={800} aria-label="Policy" />
      {draft.source_excerpt && <p className="text-xs text-muted-foreground">From your document: “{draft.source_excerpt}”</p>}
      {draft.review_notes.map((note) => (
        <p key={note} className="text-xs" style={{ color: "var(--status-pending-strong)" }}>
          {note}
        </p>
      ))}
      {replaces && <p className="text-xs text-muted-foreground">Approving this replaces the current {label.toLowerCase()} policy.</p>}
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={approve} disabled={busy || !text.trim()}>
          Approve
        </Button>
        <Button size="sm" variant="ghost" onClick={discard} disabled={busy}>
          Discard
        </Button>
      </div>
    </li>
  );
}

export function PoliciesTab({
  overview,
  loading,
  onChanged,
}: {
  overview: PolicyOverview | null;
  loading: boolean;
  onChanged: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [approvingAll, setApprovingAll] = useState(false);

  async function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    try {
      const result = await api.policies.upload(file);
      const found = result.drafts.length;
      const extra = result.skipped ? ` ${result.skipped} already on file.` : "";
      if (found) toast.success(`Found ${found} ${found === 1 ? "policy" : "policies"} to review.${extra}`);
      else toast.info(result.skipped ? `Everything in this file is already on file.` : "Couldn't find any policies in this file.");
      if (result.document.truncated) toast.warning("This file was long, so only the first part was read.");
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't read this file"));
    } finally {
      setUploading(false);
    }
  }

  async function approveAll() {
    setApprovingAll(true);
    try {
      const { approved } = await api.policies.approveAll();
      toast.success(`Approved ${approved} ${approved === 1 ? "policy" : "policies"}.`);
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't approve"));
    } finally {
      setApprovingAll(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h2 className="text-base font-semibold">Policies</h2>
          <p className="max-w-xl text-sm text-muted-foreground">
            Mira answers guests from these. Upload your booking terms and she suggests policies for you to check, or write them yourself.
            Nothing is used until you approve it.
          </p>
        </div>
        <div className="shrink-0">
          <input ref={fileRef} type="file" accept={ACCEPT} className="hidden" onChange={handleFile} />
          <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={uploading}>
            <Upload className="mr-1.5 size-4" />
            {uploading ? "Reading…" : "Upload a document"}
          </Button>
          <p className="mt-1 text-xs text-muted-foreground">PDF, Word, Excel, CSV, text or a photo</p>
        </div>
      </div>

      {loading && !overview ? (
        <Skeleton className="h-64 w-full" />
      ) : overview ? (
        <>
          {overview.drafts.length > 0 && (
            <Card>
              <CardContent className="space-y-1 pt-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">To review ({overview.drafts.length})</p>
                    <p className="text-xs text-muted-foreground">Mira read these from your document. Check each one before she uses it.</p>
                  </div>
                  {overview.drafts.length > 1 && (
                    <Button size="sm" variant="outline" onClick={approveAll} disabled={approvingAll}>
                      Approve all
                    </Button>
                  )}
                </div>
                <ul className="divide-y">
                  {overview.drafts.map((draft) => (
                    <DraftCard key={draft.id} draft={draft} overview={overview} onChanged={onChanged} />
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardContent className="space-y-3 pt-4">
              <Legend />
              <ul className="divide-y">
                {overview.rows.map((row) => (
                  <TopicRow key={row.topic} row={row} overview={overview} onChanged={onChanged} />
                ))}
              </ul>
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
