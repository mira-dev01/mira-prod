"use client";

import { useRef, useState } from "react";
import { Check, Pencil, Plus, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Cell, Hint, Sheet, Toolbar } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { ALL_PROPERTIES, PropertyScopePicker, type PropertyScope } from "@/components/property-scope-picker";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PolicyOut, PolicyOverview } from "@/lib/types";

const ACCEPT = ".pdf,.docx,.xlsx,.csv,.txt,.md,.png,.jpg,.jpeg,.webp";
const OTHER = "other";

// The one separator used between entries: a soft rule.
const LINE = "border-b border-border last:border-b-0";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

type Topics = PolicyOverview["topics"];

function topicLabel(topics: Topics, key: string, title?: string): string {
  if (key === OTHER && title) return title;
  return topics.find((t) => t.key === key)?.label ?? key;
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

function TopicSelect({ topics, value, onChange }: { topics: Topics; value: string; onChange: (key: string) => void }) {
  return (
    <Select value={value} onValueChange={(v) => v && onChange(v)}>
      <SelectTrigger className="h-8 w-56 text-xs" aria-label="Topic">
        <SelectValue>{value === OTHER ? "Something else" : topicLabel(topics, value)}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        {topics.map((t) => (
          <SelectItem key={t.key} value={t.key}>
            {t.key === OTHER ? "Something else" : t.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function SectionHeader({
  tone,
  title,
  count,
  children,
}: {
  tone: "pending" | "live";
  title: string;
  count: number;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h2 className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.1em] text-foreground">
        <span
          aria-hidden
          className="size-2 rounded-full"
          style={{
            backgroundColor: tone === "live" ? "var(--status-live)" : "var(--status-pending)",
          }}
        />
        {title}
        <span className="font-normal text-muted-foreground">{count}</span>
      </h2>
      {children}
    </div>
  );
}

/** One approved policy: plain text with small edit/delete icons. Edit turns the text into a field. */
function LiveEntry({
  policy,
  topics,
  unused,
  waitingOn,
  onChanged,
}: {
  policy: PolicyOut;
  topics: Topics;
  unused: boolean;
  /** Names of the properties this policy is waiting to be approved for. */
  waitingOn: string[];
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [text, setText] = useState(policy.text);
  const [title, setTitle] = useState(policy.title);
  const [topic, setTopic] = useState(policy.topic);
  const [busy, setBusy] = useState(false);
  const isOther = policy.topic === OTHER;
  const dirty = text.trim() !== policy.text || topic !== policy.topic || (isOther && title.trim() !== policy.title);

  function cancel() {
    setText(policy.text);
    setTitle(policy.title);
    setTopic(policy.topic);
    setEditing(false);
  }

  async function save() {
    setBusy(true);
    try {
      await api.policies.update(policy.id, {
        text,
        ...(isOther ? { title } : {}),
        ...(topic !== policy.topic ? { topic } : {}),
      });
      toast.success("Saved. Mira will use this on her next call.");
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
      await api.policies.remove(policy.id);
      onChanged();
    } catch (err) {
      toast.error(errorMessage(err, "Couldn't remove"));
      setBusy(false);
    }
  }

  const scope = policy.property_name ? `Only ${policy.property_name}` : "All properties";

  if (editing) {
    return (
      <li className={cn("space-y-2 py-4", LINE)}>
        <div className="flex flex-wrap items-center gap-2">
          <TopicSelect topics={topics} value={topic} onChange={setTopic} />
          <span className="text-xs text-muted-foreground">{scope}</span>
        </div>
        {isOther && (
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Short title"
            maxLength={120}
            aria-label="Title"
          />
        )}
        <Textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={800}
          aria-label="Policy"
        />
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={save} disabled={busy || !dirty || !text.trim()}>
            Save
          </Button>
          <Button size="sm" variant="ghost" onClick={cancel} disabled={busy}>
            Cancel
          </Button>
        </div>
      </li>
    );
  }

  return (
    <li className={cn("flex items-start gap-3 py-4", LINE)}>
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-xs text-muted-foreground">
          {topicLabel(topics, policy.topic, policy.title)} · {scope}
        </p>
        <p className="text-sm">{policy.text}</p>
        {unused && waitingOn.length > 0 && (
          <p className="pt-1 text-xs" style={{ color: "var(--status-pending-strong)" }}>
            Not used yet: waiting for your approval on {waitingOn.join(", ")}. Open the property to approve, edit or
            delete it there.
          </p>
        )}
        {unused && waitingOn.length === 0 && (
          <p className="pt-1 text-xs" style={{ color: "var(--status-pending-strong)" }}>
            Not used yet: this is about a place that isn&apos;t one of your properties. Add that property, or edit this
            to name one of yours, or delete it.
          </p>
        )}
      </div>
      {confirming ? (
        <div className="flex shrink-0 items-center gap-2 pt-0.5 text-xs">
          <span className="text-muted-foreground">Delete this?</span>
          <button
            type="button"
            className="font-medium text-destructive hover:underline"
            onClick={remove}
            disabled={busy}
          >
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
          <IconButton label="Edit" onClick={() => setEditing(true)}>
            <Pencil className="size-3.5" />
          </IconButton>
          <IconButton label="Delete" onClick={() => setConfirming(true)}>
            <Trash2 className="size-3.5" />
          </IconButton>
        </div>
      )}
    </li>
  );
}

/** One policy read from a document, waiting for the host. Same look; the scope is a live control. */
function DraftEntry({
  draft,
  overview,
  onChanged,
}: {
  draft: PolicyOut;
  overview: PolicyOverview;
  onChanged: () => void;
}) {
  const initialScope: PropertyScope = draft.property_id ? { all: false, ids: [draft.property_id] } : ALL_PROPERTIES;
  const [scope, setScope] = useState<PropertyScope>(initialScope);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(draft.text);
  const [topic, setTopic] = useState(draft.topic);
  const [busy, setBusy] = useState(false);
  const label = topicLabel(overview.topics, topic, draft.title);
  const manyProperties = overview.properties.length > 1;

  const scopeChanged = scope.all !== initialScope.all || (!scope.all && scope.ids.join() !== initialScope.ids.join());
  const targets: (string | null)[] = scope.all ? [null] : scope.ids;
  const row = overview.rows.find((r) => r.topic === topic);
  const replaces = topic !== OTHER && Boolean(row?.items.some((i) => targets.includes(i.property_id)));

  async function approve() {
    setBusy(true);
    try {
      if (text.trim() !== draft.text || topic !== draft.topic) await api.policies.update(draft.id, { text, topic });
      await api.policies.approve(
        draft.id,
        scopeChanged ? (scope.all ? { apply_to_all: true } : { property_ids: scope.ids }) : undefined,
      );
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
    <li className={cn("space-y-2 py-4", LINE)}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            {editing ? (
              <TopicSelect topics={overview.topics} value={topic} onChange={setTopic} />
            ) : (
              <span>{label}</span>
            )}
            <span aria-hidden>·</span>
            {manyProperties ? (
              <PropertyScopePicker
                value={scope}
                onChange={setScope}
                properties={overview.properties}
                inclusive
                variant="link"
              />
            ) : (
              <span>Your property</span>
            )}
          </div>
          {editing ? (
            <Textarea
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={800}
              aria-label="Policy"
            />
          ) : (
            <p className="text-sm">{text}</p>
          )}
        </div>
        <IconButton label={editing ? "Done editing" : "Edit"} onClick={() => setEditing((v) => !v)} disabled={busy}>
          {editing ? <Check className="size-3.5" /> : <Pencil className="size-3.5" />}
        </IconButton>
      </div>
      {draft.source_excerpt && (
        <p className="text-xs text-muted-foreground">From your document: “{draft.source_excerpt}”</p>
      )}
      {draft.review_notes.map((note) => (
        <p key={note} className="text-xs" style={{ color: "var(--status-pending-strong)" }}>
          {note}
        </p>
      ))}
      {replaces && (
        <p className="text-xs text-muted-foreground">
          Approving this replaces the current {label.toLowerCase()} policy.
        </p>
      )}
      <div className="flex items-center gap-2 pt-1">
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

/** Add a policy from anywhere: pick a topic (or write your own title), say which properties it covers. */
function AddPolicyForm({
  overview,
  onDone,
  onCancel,
}: {
  overview: PolicyOverview;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [topic, setTopic] = useState(overview.topics[0]?.key ?? "check_in_out");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [scope, setScope] = useState<PropertyScope>(ALL_PROPERTIES);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const custom = topic === OTHER;
  const manyProperties = overview.properties.length > 1;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.policies.createScoped({
        topic,
        text,
        ...(custom ? { title } : {}),
        ...(manyProperties && !scope.all ? { property_ids: scope.ids } : {}),
      });
      toast.success("Saved. Mira will use this on her next call.");
      onDone();
    } catch (err) {
      setError(errorMessage(err, "Couldn't save"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <TopicSelect topics={overview.topics} value={topic} onChange={setTopic} />
        {manyProperties && (
          <>
            <span aria-hidden>·</span>
            <PropertyScopePicker
              value={scope}
              onChange={setScope}
              properties={overview.properties}
              inclusive
              variant="link"
            />
          </>
        )}
      </div>
      {custom && (
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title, e.g. Parking"
          maxLength={120}
          aria-label="Title"
        />
      )}
      <Textarea
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={800}
        placeholder="Write it the way a guest should hear it"
        aria-label="Policy"
      />
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={busy || !text.trim() || (custom && !title.trim())}>
          Save
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
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
  const [adding, setAdding] = useState(false);
  // Shown on the page until the next upload: a toast is gone before it can be read.
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const result = await api.policies.upload(file);
      const found = result.drafts.length;
      const extra = result.skipped ? ` ${result.skipped} already on file.` : "";
      if (found) toast.success(`Found ${found} ${found === 1 ? "policy" : "policies"} to review.${extra}`);
      else
        toast.info(
          result.skipped ? `Everything in this file is already on file.` : "Couldn't find any policies in this file.",
        );
      if (result.document.truncated) toast.warning("Part of this file couldn't be read, so check the list for gaps.");
      onChanged();
    } catch (err) {
      setUploadError(errorMessage(err, "Couldn't read this file"));
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

  const live = overview ? overview.rows.flatMap((row) => row.items) : [];
  // A host-wide policy no property carries (it names somewhere that isn't theirs) is not spoken anywhere.
  const followed = new Set(overview?.properties.flatMap((p) => p.policy_ids) ?? []);
  // Held back because they name a place the host has no property for at all.
  const unmappedIds = new Set(overview?.unmapped_policy_ids ?? []);
  const unmapped = live.filter((policy) => unmappedIds.has(policy.id));

  return (
    <Sheet>
      <Toolbar className="justify-between gap-y-4">
        <div className="max-w-md space-y-1">
          <Hint>Nothing is used until you approve it. Upload a PDF, Word, Excel, CSV, text file or photo.</Hint>
          {uploading && <Hint>Long documents are read in parts, so this can take a minute.</Hint>}
          {uploadError && (
            <p role="alert" className="text-sm text-destructive">
              {uploadError}
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          <input ref={fileRef} type="file" accept={ACCEPT} className="hidden" onChange={handleFile} />
          <Button variant="outline" onClick={() => setAdding(true)} disabled={!overview}>
            <Plus className="mr-1.5 size-4" />
            Add a policy
          </Button>
          <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={uploading}>
            <Upload className="mr-1.5 size-4" />
            {uploading ? "Reading…" : "Upload a document"}
          </Button>
        </div>
      </Toolbar>

      {loading && !overview ? (
        <Cell>
          <Skeleton className="h-64 w-full" />
        </Cell>
      ) : overview ? (
        <>
          {adding && (
            <Cell>
              <AddPolicyForm
                overview={overview}
                onDone={() => {
                  setAdding(false);
                  onChanged();
                }}
                onCancel={() => setAdding(false)}
              />
            </Cell>
          )}

          {overview.drafts.length > 0 && (
            <Cell className="gap-1" aria-label="Awaiting your approval">
              <SectionHeader tone="pending" title="Awaiting your approval" count={overview.drafts.length}>
                {overview.drafts.length > 1 && (
                  <Button size="sm" variant="outline" onClick={approveAll} disabled={approvingAll}>
                    Approve all
                  </Button>
                )}
              </SectionHeader>
              <ul>
                {overview.drafts.map((draft) => (
                  <DraftEntry key={draft.id} draft={draft} overview={overview} onChanged={onChanged} />
                ))}
              </ul>
            </Cell>
          )}

          {unmapped.length > 0 && (
            <Cell className="gap-1" aria-label="Policies that need a property">
              <SectionHeader tone="pending" title="Needs a property" count={unmapped.length} />
              <Hint>
                These name a place that isn&apos;t one of your properties, so Mira uses them for none. Add that
                property (they will then wait for your approval in its pane), or edit the policy to name one of yours.
              </Hint>
              <ul>
                {unmapped.map((policy) => (
                  <li key={policy.id} className={cn("py-3 text-sm", LINE)}>
                    <p className="text-xs text-muted-foreground">{topicLabel(overview.topics, policy.topic, policy.title)}</p>
                    <p>{policy.text}</p>
                    {policy.review_notes.map((note) => (
                      <p key={note} className="pt-0.5 text-xs" style={{ color: "var(--status-pending-strong)" }}>
                        {note}
                      </p>
                    ))}
                  </li>
                ))}
              </ul>
            </Cell>
          )}

          <Cell className="gap-1" aria-label="Approved policies">
            <SectionHeader tone="live" title="Approved · live on calls" count={live.length} />
            {live.length === 0 ? (
              <p className="pt-2 text-sm text-muted-foreground">
                No approved policies yet. Add one, or upload a document and approve what Mira finds.
              </p>
            ) : (
              <ul>
                {live.map((policy) => (
                  <LiveEntry
                    key={policy.id}
                    policy={policy}
                    topics={overview.topics}
                    unused={
                      !policy.property_id &&
                      overview.properties.length > 0 &&
                      !followed.has(policy.id) &&
                      policy.review_notes.some((note) => note.includes("isn't one of your properties"))
                    }
                    waitingOn={overview.properties.filter((p) => p.pending_policy_ids.includes(policy.id)).map((p) => p.name)}
                    onChanged={onChanged}
                  />
                ))}
              </ul>
            )}
          </Cell>
        </>
      ) : null}
    </Sheet>
  );
}
