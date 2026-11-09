"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Cell, CellTitle, FieldRow, Sheet, WideCell } from "@/components/ui/sheet";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { ExpandableText } from "@/components/expandable-text";
import { Input } from "@/components/ui/input";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusChip } from "@/components/status-chip";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { matchesSearch } from "@/lib/utils";
import { UnansweredQuestionsCard } from "@/components/unanswered-questions-card";
import {
  ALL_PROPERTIES,
  PropertyScopePicker,
  scopeFromPropertyIds,
  scopeLabel,
  scopePayload,
  type PropertyScope,
} from "@/components/property-scope-picker";
import type { FaqEntryOut } from "@/lib/types";

type FaqGroup = { entry: FaqEntryOut; rows: FaqEntryOut[] };

/** Collapses the rows of one FAQ applied to several properties into one item. */
function groupEntries(entries: FaqEntryOut[]): FaqGroup[] {
  const byGroup = new Map<string, FaqGroup>();
  const out: FaqGroup[] = [];
  for (const entry of entries) {
    if (!entry.group_id) {
      out.push({ entry, rows: [entry] });
      continue;
    }
    const existing = byGroup.get(entry.group_id);
    if (existing) {
      existing.rows.push(entry);
    } else {
      const group = { entry, rows: [entry] };
      byGroup.set(entry.group_id, group);
      out.push(group);
    }
  }
  return out;
}

function sameScope(a: PropertyScope, b: PropertyScope): boolean {
  if (a.all || b.all) return a.all === b.all;
  return a.ids.length === b.ids.length && a.ids.every((id) => b.ids.includes(id));
}

function scopeText(faq: FaqGroup, properties: { id: string; name: string }[] | null | undefined): string {
  const scope = scopeFromPropertyIds(faq.rows.map((row) => row.property_id));
  if (scope.all || scope.ids.length === 1) return scopeLabel(scope, properties);
  return scope.ids.map((id) => properties?.find((p) => p.id === id)?.name ?? "Property").join(", ");
}

export default function FaqPage() {
  const { data: properties } = useAsync(() => api.properties.list(), []);
  const { data: entries, loading, refetch } = useAsync(() => api.faq.list(), []);

  const [addOpen, setAddOpen] = useState(false);
  // The same panel adds a new entry or edits an existing one.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [scope, setScope] = useState<PropertyScope>(ALL_PROPERTIES);
  // The scope the entry had when the panel opened, to know if it changed.
  const [originalScope, setOriginalScope] = useState<PropertyScope>(ALL_PROPERTIES);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [category, setCategory] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [search, setSearch] = useState("");

  // One logical FAQ can be stored as several rows (one per property, sharing
  // a group_id). The table shows it once, with the full list of properties.
  const faqs = groupEntries(entries ?? []);
  const scopeOf = (faq: FaqGroup) => scopeFromPropertyIds(faq.rows.map((row) => row.property_id));

  const filteredEntries = faqs.filter((faq) =>
    matchesSearch(search, [
      faq.entry.question,
      faq.entry.answer,
      scopeLabel(scopeOf(faq), properties),
      faq.entry.category,
      faq.entry.status,
    ])
  );

  function resetForm() {
    setQuestion("");
    setAnswer("");
    setCategory("");
    setScope(ALL_PROPERTIES);
    setOriginalScope(ALL_PROPERTIES);
    setEditingId(null);
  }

  function openAdd() {
    resetForm();
    setAddOpen(true);
  }

  function openEdit(faq: FaqGroup) {
    const { entry } = faq;
    const current = scopeOf(faq);
    setEditingId(entry.id);
    setQuestion(entry.question);
    setAnswer(entry.answer);
    setCategory(entry.category ?? "");
    setScope(current);
    setOriginalScope(current);
    setAddOpen(true);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    const fields = { question, answer, category: category || null };
    try {
      if (editingId) {
        // Editing keeps the entry's status (verified stays verified). Content
        // edits apply to every property the FAQ covers; the scope is its own call.
        await api.faq.update(editingId, fields);
        if (!sameScope(scope, originalScope)) await api.faq.setScope(editingId, scopePayload(scope));
        toast.success("FAQ entry updated");
      } else {
        await api.faq.create({
          ...fields,
          ...(scope.all ? { property_id: null } : { property_ids: scope.ids }),
          status: "verified",
          verified_by: "host",
        });
        toast.success("FAQ entry added");
      }
      resetForm();
      setAddOpen(false);
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : editingId ? "Failed to update FAQ entry" : "Failed to add FAQ entry");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggleVerified(id: string, currentStatus: string) {
    try {
      await api.faq.update(id, { status: currentStatus === "verified" ? "pending" : "verified", verified_by: "host" });
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update FAQ entry");
    }
  }

  async function handleDelete(id: string) {
    try {
      // Removing from this page removes the whole FAQ, from every property.
      await api.faq.remove(id, { group: true });
      toast.success("FAQ entry removed");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove FAQ entry");
    }
  }

  return (
    <div className="min-w-0 space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <h1 className="page-title">FAQ knowledge base</h1>
        <Button variant="cta" className="self-start" onClick={openAdd}>
          <Plus className="size-4" />
          Add new
        </Button>
      </div>

      <Sheet>
        <UnansweredQuestionsCard inSheet collapsible className="md:col-span-12" />

        <Cell className="min-w-0 gap-0 p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
            <CellTitle>Verified FAQ entries</CellTitle>
            {entries && entries.length > 0 && (
              <div className="w-full sm:w-64">
                <Input
                  placeholder="Search FAQ…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  leadingIcon={<Search />}
                />
              </div>
            )}
          </div>
          {loading ? (
            <Skeleton className="m-5 h-64" />
          ) : !entries || entries.length === 0 ? (
            <p className="p-5 text-sm text-muted-foreground">No FAQ entries yet.</p>
          ) : filteredEntries.length === 0 ? (
            <p className="p-5 text-sm text-muted-foreground">No FAQ entries match your search.</p>
          ) : (
            <div className="max-h-[32rem] min-w-0 overflow-auto px-3 py-2">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Question</TableHead>
                    <TableHead>Answer</TableHead>
                    <TableHead>Applies to</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="w-48" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredEntries.map((faq) => {
                    const entry = faq.entry;
                    return (
                      <TableRow key={entry.id} className="align-top">
                        <TableCell className="w-[180px] max-w-[180px] py-3">
                          <ExpandableText text={entry.question} maxLength={60} />
                        </TableCell>
                        <TableCell className="w-[220px] max-w-[220px] py-3 text-muted-foreground">
                          <ExpandableText text={entry.answer} maxLength={90} />
                        </TableCell>
                        <TableCell className="py-3 break-words whitespace-normal">{scopeText(faq, properties)}</TableCell>
                        <TableCell className="py-3 capitalize">{entry.category ?? "—"}</TableCell>
                        <TableCell className="py-3">
                          <StatusChip status={entry.status} tone={entry.status === "verified" ? "live" : "pending"} />
                        </TableCell>
                        <TableCell className="py-3">
                          <div className="flex flex-wrap gap-2">
                            <Button variant="outline" size="sm" onClick={() => openEdit(faq)}>
                              Edit
                            </Button>
                            <Button variant="outline" size="sm" onClick={() => handleToggleVerified(entry.id, entry.status)}>
                              {entry.status === "verified" ? "Unverify" : "Verify"}
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => handleDelete(entry.id)}>
                              Remove
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </Cell>
      </Sheet>

      <RightPanel
        open={addOpen}
        onOpenChange={(open) => {
          setAddOpen(open);
          if (!open) resetForm();
        }}
        title={editingId ? "Edit FAQ entry" : "Add FAQ entry"}
        footer={
          <RightPanelFooterButton type="submit" form="add-faq-form" disabled={submitting}>
            {submitting ? "Saving…" : editingId ? "Save changes" : "Add (verified)"}
          </RightPanelFooterButton>
        }
      >
        <form id="add-faq-form" onSubmit={handleSubmit}>
          <Sheet>
            <WideCell>
              <FieldRow label="Applies to" htmlFor="faq-scope">
                <PropertyScopePicker id="faq-scope" value={scope} onChange={setScope} properties={properties} />
              </FieldRow>
              <FieldRow label="Category" htmlFor="category">
                <Input id="category" placeholder="e.g. wifi, parking" value={category} onChange={(e) => setCategory(e.target.value)} />
              </FieldRow>
              <FieldRow label="Question" htmlFor="question" required>
                <Input id="question" required value={question} onChange={(e) => setQuestion(e.target.value)} />
              </FieldRow>
              <FieldRow label="Answer" htmlFor="answer" required>
                <DictationTextarea id="answer" required value={answer} onValueChange={setAnswer} />
              </FieldRow>
            </WideCell>
          </Sheet>
        </form>
      </RightPanel>
    </div>
  );
}
