"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowRight, ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { ListRow } from "@/components/ui/list-row";
import { RightPanel, RightPanelFooterButton } from "@/components/ui/right-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
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
}: { limit?: number; linkToFaqPage?: boolean; collapsible?: boolean } = {}) {
  const { data: properties } = useAsync(() => api.properties.list(), []);
  const { data: allGaps, loading: gapsLoading, refetch: refetchGaps } = useAsync(() => api.faqGaps.list(), []);
  const gaps = limit ? allGaps?.slice(0, limit) : allGaps;
  const totalCount = allGaps?.length ?? 0;

  const [expanded, setExpanded] = useState(!collapsible);

  const propertyName = (id: string | null) =>
    id ? properties?.find((p) => p.id === id)?.name ?? id : "All properties";

  const [answeringGap, setAnsweringGap] = useState<FaqGapOut | null>(null);
  const [gapAnswerText, setGapAnswerText] = useState("");
  const [applyToProperty, setApplyToProperty] = useState(false);
  const [answeringSubmitting, setAnsweringSubmitting] = useState(false);

  const [isRecording, setIsRecording] = useState(false);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [recordedUrl, setRecordedUrl] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  function openAnswerDialog(gap: FaqGapOut) {
    setAnsweringGap(gap);
    // Pre-fill from Knowledge Memory's semantic match, if one was found
    // (memory-architecture-plan.md section 3.2) -- the host can edit or
    // clear it before saving, same "AI suggests, host approves" pattern as
    // the Host Memory discount rules on the AI Training page.
    setGapAnswerText(gap.suggested_answer ?? "");
    setApplyToProperty(false);
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
      if (recordedBlob) {
        await api.faqGaps.answerVoice(answeringGap.sample_id, recordedBlob, applyToProperty);
      } else {
        await api.faqGaps.answer(answeringGap.sample_id, { answer: gapAnswerText, apply_to_property: applyToProperty });
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
      <Card className="min-w-0">
        {collapsible ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="flex w-full items-center justify-between gap-3 p-6 text-left"
          >
            <div className="min-w-0">
              <CardTitle>Questions Mira couldn&apos;t answer</CardTitle>
              {expanded && (
                <CardDescription className="mt-1.5">
                  Guests asked these and Mira had no verified answer, so she couldn&apos;t help — answer one below
                  and Mira will use it automatically next time.
                </CardDescription>
              )}
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
            <CardTitle>Questions Mira couldn&apos;t answer</CardTitle>
            <CardDescription>
              Guests asked these and Mira had no verified answer, so she couldn&apos;t help — answer one below and
              Mira will use it automatically next time.
            </CardDescription>
          </CardHeader>
        )}
        {expanded && <CardContent className="min-w-0">
          {gapsLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : !gaps || gaps.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No unanswered questions right now — nice work keeping the FAQ knowledge base current.
            </p>
          ) : (
            <div className="space-y-3">
              {gaps.map((gap) => (
                <ListRow key={gap.sample_id} className="flex-row flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="min-w-0 break-words text-sm font-medium">{gap.question}</p>
                      <Badge variant="destructive" className="shrink-0">
                        asked {gap.count}× {gap.count === 1 ? "time" : "times"}
                      </Badge>
                      {gap.suggested_answer && (
                        <Badge variant="secondary" className="shrink-0">
                          Suggested answer found
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
          <div className="border-t px-4 py-3">
            <Link
              href="/dashboard/faq"
              className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              View all {totalCount} unanswered question{totalCount === 1 ? "" : "s"}
              <ArrowRight className="size-3.5" />
            </Link>
          </div>
        )}
      </Card>

      <RightPanel
        open={!!answeringGap}
        onOpenChange={(open) => {
          if (!open) setAnsweringGap(null);
        }}
        title={<>Answer — {answeringGap?.question}</>}
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
            <p className="text-xs text-muted-foreground">
              This looks similar to a question you already answered — pre-filled below, edit or clear it if it
              doesn&apos;t fit.
            </p>
          )}
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

          {answeringGap?.property_id && (
            <div className="flex items-center gap-2">
              <Switch id="apply-to-property" checked={applyToProperty} onCheckedChange={setApplyToProperty} />
              <Label htmlFor="apply-to-property" className="text-sm text-muted-foreground">
                Apply only to {propertyName(answeringGap.property_id)} (otherwise applies portfolio-wide)
              </Label>
            </div>
          )}
        </form>
      </RightPanel>
    </>
  );
}
