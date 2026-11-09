"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DictationTextarea } from "@/components/ui/dictation-textarea";
import { Input } from "@/components/ui/input";
import { PropertyFormSection } from "@/components/property-form-section";
import { api, ApiError } from "@/lib/api";
import type { FaqEntryOut } from "@/lib/types";

/**
 * Per-property FAQ editor, mirroring PropertyPhotosManager's pattern: writes
 * straight to the real backend (POST/PATCH/DELETE /faq, scoped to this
 * property_id) instead of staging into the property form's local state, so
 * these questions land in the same FaqEntry table the dashboard's global FAQ
 * tab (app/dashboard/faq) reads -- previously this section wrote into the
 * legacy Property.faq JSON column, invisible to that tab.
 */
export function PropertyFaqManager({
  propertyId,
  entries,
  onChange,
}: {
  propertyId: string;
  entries: FaqEntryOut[];
  onChange: (next: FaqEntryOut[]) => void;
}) {
  const [savingId, setSavingId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  async function addQuestion() {
    setAdding(true);
    try {
      const created = await api.faq.create({
        property_id: propertyId,
        question: "",
        answer: "",
        status: "verified",
        verified_by: "host",
      });
      onChange([created, ...entries]);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to add question");
    } finally {
      setAdding(false);
    }
  }

  function updateField(entry: FaqEntryOut, patch: { question?: string; answer?: string }) {
    onChange(entries.map((e) => (e.id === entry.id ? { ...e, ...patch } : e)));
  }

  async function commitField(entry: FaqEntryOut, patch: { question?: string; answer?: string }) {
    setSavingId(entry.id);
    try {
      const updated = await api.faq.update(entry.id, patch);
      onChange(entries.map((e) => (e.id === entry.id ? updated : e)));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save question");
    } finally {
      setSavingId(null);
    }
  }

  async function removeQuestion(entry: FaqEntryOut) {
    setSavingId(entry.id);
    try {
      await api.faq.remove(entry.id);
      onChange(entries.filter((e) => e.id !== entry.id));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove question");
    } finally {
      setSavingId(null);
    }
  }

  return (
    <PropertyFormSection
      title="FAQ"
      count={entries.length}
      action={
        <Button type="button" variant="outline" size="sm" disabled={adding} onClick={addQuestion}>
          {adding ? "Adding…" : "+ Add question"}
        </Button>
      }
      helpText="Also shown on the FAQ page, tagged to this property."
    >
      {entries.length === 0 ? (
        <p className="p-5 text-sm text-muted-foreground">No FAQ entries yet.</p>
      ) : (
        <div className="px-5">
          {entries.map((entry) => (
            <div key={entry.id} className="space-y-3 border-b border-border py-4 last:border-0">
              <Input
                placeholder="Question"
                aria-label="Question"
                value={entry.question}
                onChange={(e) => updateField(entry, { question: e.target.value })}
                onBlur={(e) => commitField(entry, { question: e.target.value })}
              />
              <DictationTextarea
                placeholder="Answer"
                aria-label="Answer"
                value={entry.answer}
                onValueChange={(value) => updateField(entry, { answer: value })}
                onBlur={(e) => commitField(entry, { answer: (e.target as HTMLTextAreaElement).value })}
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-destructive"
                disabled={savingId === entry.id}
                onClick={() => removeQuestion(entry)}
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
      )}
    </PropertyFormSection>
  );
}
