"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowRight, ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ListRow } from "@/components/ui/list-row";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { CellCard, CellTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import { PropertyScopePicker, scopePayload, type PropertyScope } from "@/components/property-scope-picker";
import type { FaqGapOut } from "@/lib/types";

// Shared by two call sites:
//  - Overview page: compact preview (limit=2), links out to the FAQ page
//    so a host sees the headline the moment they open the dashboard.
//  - FAQ page: full list (no limit, collapsible), stacked above the
//    verified FAQ entries list rather than side-by-side with it -- a
//    returning host mostly cares about the verified list below, so this
//    starts collapsed to a slim summary bar instead of taking half the page.
export function UnansweredQuestionsCard({
  limit,
  linkToFaqPage,
  collapsible,
  inSheet,
  className,
}: {
  limit?: number;
  linkToFaqPage?: boolean;
  collapsible?: boolean;
  /** Overview's compact preview sits right under the card title with no
   * room to spare -- the explanatory sentence is redundant there since
   * "Questions Mira couldn't answer" + the unanswered-count badge already
   * say what this card is. The FAQ page's full view keeps it. */
  /** Render as a block of the Overview's Sheet (shared ruled edges) instead of a standalone Card. */
  inSheet?: boolean;
  /** Grid placement when inSheet (e.g. "md:col-span-6"). */
  className?: string;
} = {}) {
  const Shell = inSheet ? CellCard : Card;
  const Title = inSheet ? CellTitle : CardTitle;
  const { data: properties } = useAsync(() => api.properties.list(), []);
  const { data: allGaps, loading: gapsLoading, refetch: refetchGaps } = useAsync(() => api.faqGaps.list(), []);
  // Which property's gaps to show (full list only). Filtered client-side: the
  // list is already fully loaded and small, and it keeps the unanswered-count
  // badge (totalCount, across every property) unaffected by the filter.
  const [selectedFilterId, setSelectedFilterId] = useState<string>("all");
  const gapPropertyIds = Array.from(new Set((allGaps ?? []).map((gap) => gap.property_id).filter(Boolean))) as string[];
  // Falls back to "all" once the chosen property has no gaps left (e.g. the
  // host just answered its last one) -- otherwise the list would empty out
  // with the filter control gone, leaving no way back.
  const filterPropertyId = gapPropertyIds.includes(selectedFilterId) ? selectedFilterId : "all";
  const visibleGaps =
    filterPropertyId === "all" ? allGaps : allGaps?.filter((gap) => gap.property_id === filterPropertyId);
  const gaps = limit ? visibleGaps?.slice(0, limit) : visibleGaps;
  const totalCount = allGaps?.length ?? 0;

  const [expanded, setExpanded] = useState(!collapsible);

  const propertyName = (id: string | null) =>
    id ? properties?.find((p) => p.id === id)?.name ?? id : "All properties";

  const [answeringGap, setAnsweringGap] = useState<FaqGapOut | null>(null);
  const [gapAnswerText, setGapAnswerText] = useState("");
  // The host can reword the guest's question before it becomes an FAQ.
  const [gapQuestionText, setGapQuestionText] = useState("");
  const [scope, setScope] = useState<PropertyScope>({ all: true, ids: [] });
  const [answeringSubmitting, setAnsweringSubmitting] = useState(false);

  const [isRecording, setIsRecording] = useState(false);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [recordedUrl, setRecordedUrl] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  function openAnswerDialog(gap: FaqGapOut) {
    setAnsweringGap(gap);
    setGapQuestionText(gap.question);
    // Pre-fill from Knowledge Memory's semantic match, if one was found
    // (memory-architecture-plan.md section 3.2) -- the host can edit or
    // clear it before saving, same "AI suggests, host approves" pattern as
    // the Host Memory discount rules on the AI Training page.
    setGapAnswerText(gap.suggested_answer ?? "");
    // The gap belongs to one property: default the answer to that property
    // only. A portfolio-wide answer ("pets are allowed") would otherwise
    // become a fact Mira states for every other property too. The host can
    // widen it from the dropdown.
    setScope(gap.property_id ? { all: false, ids: [gap.property_id] } : { all: true, ids: [] });
    setRecordedBlob(null);
    setRecordedUrl(null);
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        setRecordedBlob(blob);
        setRecordedUrl(URL.createObjectURL(blob));
        stream.getTracks().forEach((track) => track.stop());
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch {
      toast.error("Could not access the microphone -- check browser permissions");
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    setIsRecording(false);
  }

  async function handleAnswerGap(e: React.FormEvent) {
    e.preventDefault();
    if (!answeringGap) return;
    setAnsweringSubmitting(true);
    try {
      const question = gapQuestionText.trim() || undefined;
      if (recordedBlob) {
        await api.faqGaps.answerVoice(answeringGap.sample_id, recordedBlob, scopePayload(scope), question);
      } else {
        await api.faqGaps.answer(answeringGap.sample_id, {
          answer: gapAnswerText,
          ...scopePayload(scope),
          question,
        });
      }
      toast.success("Saved — Mira will use this answer next time a guest asks");
      setAnsweringGap(null);
      refetchGaps();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save answer");
    } finally {
      setAnsweringSubmitting(false);
    }
  }

  return (
    <>
      <Shell className={cn("h-full min-w-0", inSheet && "md:col-span-6", className)}>
        {collapsible ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="flex w-full items-center justify-between gap-3 p-6 text-left"
          >
            <div className="min-w-0">
              <Title>Questions Mira couldn&apos;t answer</Title>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              {!gapsLoading && totalCount > 0 && (
                <Badge variant="destructive">
                  {totalCount} unanswered
                </Badge>
              )}
              <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", expanded && "rotate-180")} />
            </div>
          </button>
        ) : (
          <CardHeader>
            <Title>Questions Mira couldn&apos;t answer</Title>
          </CardHeader>
        )}
        {expanded && <CardContent className="min-w-0 flex-1">
          {gapsLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : !gaps || gaps.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No unanswered questions.
            </p>
          ) : (
            <div className="space-y-3">
              {!limit && gapPropertyIds.length > 1 && (
                <Select value={filterPropertyId} onValueChange={(v) => v && setSelectedFilterId(v)}>
                  <SelectTrigger className="w-full sm:w-64">
                    <SelectValue>
                      {(value: string) => (value === "all" ? "All properties" : propertyName(value))}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All properties</SelectItem>
                    {gapPropertyIds.map((id) => (
                      <SelectItem key={id} value={id}>
                        {propertyName(id)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              {gaps.map((gap) => (
                <ListRow key={gap.sample_id} className="flex-row flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="min-w-0 break-words text-sm font-medium">{gap.question}</p>
                      <Badge variant="destructive" className="shrink-0">
                        asked {gap.count}×
                      </Badge>
                      {gap.suggested_answer && (
                        <Badge variant="secondary" className="shrink-0">
                          Suggested answer found
                        </Badge>
                      )}
                      {gap.source === "guard_blocked" && (
                        <Badge variant="outline" className="shrink-0">
                          Answer held back
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {propertyName(gap.property_id)} · last asked {new Date(gap.last_asked_at).toLocaleDateString()}
                    </p>
                  </div>
                  <Button size="sm" className="shrink-0" onClick={() => openAnswerDialog(gap)}>
                    {gap.suggested_answer ? "Review suggestion" : "Answer this question"}
                  </Button>
                </ListRow>
              ))}
            </div>
          )}
        </CardContent>}
        {linkToFaqPage && totalCount > 0 && (
          <div className="border-t border-border px-5 py-3">
            <Link
              href="/dashboard/faq"
              className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              View all {totalCount}
              <ArrowRight className="size-3.5" />
            </Link>
          </div>
        )}
      </Shell>

      <RightPanel
        open={!!answeringGap}
        onOpenChange={(open) => {
          if (!open) setAnsweringGap(null);
        }}
        title="Answer this question"
        footer={
          <RightPanelFooterButton
            type="submit"
            form="answer-gap-form"
            disabled={answeringSubmitting || (!gapAnswerText && !recordedBlob)}
          >
            {answeringSubmitting ? "Saving…" : "Save as FAQ entry"}
          </RightPanelFooterButton>
        }
      >
        <form id="answer-gap-form" onSubmit={handleAnswerGap} className="space-y-4">
          {answeringGap?.suggested_answer && (
            <p className="text-xs text-muted-foreground">Pre-filled from a similar answer.</p>
          )}
          <div className="space-y-2">
            <Label htmlFor="gap-question">Question</Label>
            <Input
              id="gap-question"
              required
              maxLength={500}
              value={gapQuestionText}
              onChange={(e) => setGapQuestionText(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="gap-answer">Type an answer</Label>
            <Textarea
              id="gap-answer"
              value={gapAnswerText}
              onChange={(e) => setGapAnswerText(e.target.value)}
              disabled={!!recordedBlob}
              placeholder="e.g. Yes, the pool is heated and open from 7am to 9pm."
            />
          </div>

          <div className="space-y-2 rounded-md border p-3">
            <Label>Or record a voice answer</Label>
            <div className="flex items-center gap-3">
              {!isRecording ? (
                <Button type="button" variant="outline" size="sm" onClick={startRecording} className="shrink-0">
                  {recordedBlob ? "Re-record" : "Start recording"}
                </Button>
              ) : (
                <Button type="button" variant="destructive" size="sm" onClick={stopRecording} className="shrink-0">
                  Stop recording
                </Button>
              )}
              {recordedUrl && <audio controls src={recordedUrl} className="h-8 min-w-0 flex-1" />}
            </div>
            {recordedBlob && (
              <p className="text-xs text-muted-foreground">
                Voice answer recorded -- it will be transcribed and used instead of the typed answer above.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="gap-scope">Applies to</Label>
            <PropertyScopePicker id="gap-scope" value={scope} onChange={setScope} properties={properties} />
          </div>
        </form>
      </RightPanel>
    </>
  );
}
