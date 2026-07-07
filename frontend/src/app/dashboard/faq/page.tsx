"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ExpandableText } from "@/components/expandable-text";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Sparkline } from "@/components/sparkline";
import { StatusChip } from "@/components/status-chip";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useAsync } from "@/hooks/use-async";
import { api, ApiError } from "@/lib/api";
import type { FaqGapOut } from "@/lib/types";

export default function FaqPage() {
  const { data: properties } = useAsync(() => api.properties.list(), []);
  const { data: entries, loading, refetch } = useAsync(() => api.faq.list(), []);
  const { data: gaps, loading: gapsLoading, refetch: refetchGaps } = useAsync(() => api.faqGaps.list(), []);
  const { data: gapAnalytics } = useAsync(() => api.faqGaps.analytics("week"), []);

  const [propertyId, setPropertyId] = useState<string>("all");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [category, setCategory] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const propertyName = (id: string | null) =>
    id ? properties?.find((p) => p.id === id)?.name ?? id : "All properties";

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.faq.create({
        property_id: propertyId === "all" ? null : propertyId,
        question,
        answer,
        category: category || null,
        status: "verified",
        verified_by: "host",
      });
      toast.success("FAQ entry added");
      setQuestion("");
      setAnswer("");
      setCategory("");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to add FAQ entry");
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
      await api.faq.remove(id);
      toast.success("FAQ entry removed");
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove FAQ entry");
    }
  }

  // --- Unanswered questions (FAQ Learning Engine) ---
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
    setGapAnswerText("");
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
      toast.success("Converted to a verified FAQ entry");
      setAnsweringGap(null);
      refetchGaps();
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save answer");
    } finally {
      setAnsweringSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">FAQ knowledge base</h1>
        <p className="text-sm text-muted-foreground">
          Verified answers the voice agent&apos;s search_faq tool can use — anything not verified here gets
          escalated to you instead of guessed.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Add FAQ entry</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleCreate} className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2 sm:col-span-2">
              <Label>Applies to</Label>
              <Select value={propertyId} onValueChange={(v) => v && setPropertyId(v)}>
                <SelectTrigger>
                  <SelectValue>{(value: string) => propertyName(value === "all" ? null : value)}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All properties</SelectItem>
                  {properties?.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="category">Category</Label>
              <Input id="category" placeholder="e.g. wifi, parking" value={category} onChange={(e) => setCategory(e.target.value)} />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="question">Question</Label>
              <Input id="question" required value={question} onChange={(e) => setQuestion(e.target.value)} />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="answer">Answer</Label>
              <Textarea id="answer" required value={answer} onChange={(e) => setAnswer(e.target.value)} />
            </div>
            <Button type="submit" className="sm:col-span-2" disabled={submitting}>
              Add (verified)
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Unanswered questions</CardTitle>
          <CardDescription>
            Questions the voice agent had no verified answer for, ranked by how often guests asked. Answer one to
            convert it into a real FAQ entry.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {gapAnalytics && gapAnalytics.over_time.length > 0 && (
            <div className="flex items-center gap-3 rounded-md border p-3">
              <Sparkline
                data={gapAnalytics.over_time.map((p) => p.count)}
                width={200}
                height={40}
                colorVar="--destructive"
              />
              <p className="text-sm text-muted-foreground">
                {gapAnalytics.over_time.reduce((sum, p) => sum + p.count, 0)} unanswered questions this week
              </p>
            </div>
          )}
          {gapsLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : !gaps || gaps.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No unanswered questions right now — nice work keeping the FAQ knowledge base current.
            </p>
          ) : (
            <div className="space-y-3">
              {gaps.map((gap) => (
                <div
                  key={gap.sample_id}
                  className="flex flex-wrap items-start justify-between gap-3 border-b pb-3 last:border-0 last:pb-0"
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium">{gap.question}</p>
                      <Badge variant="destructive">{gap.count}×</Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {propertyName(gap.property_id)} · last asked {new Date(gap.last_asked_at).toLocaleDateString()}
                    </p>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => openAnswerDialog(gap)}>
                    Answer
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : !entries || entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">No FAQ entries yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Question</TableHead>
              <TableHead>Answer</TableHead>
              <TableHead>Applies to</TableHead>
              <TableHead>Category</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="w-40" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((entry) => (
              <TableRow key={entry.id} className="align-top">
                <TableCell className="w-[220px] max-w-[220px] py-3">
                  <ExpandableText text={entry.question} maxLength={80} />
                </TableCell>
                <TableCell className="w-[320px] max-w-[320px] py-3 text-muted-foreground">
                  <ExpandableText text={entry.answer} maxLength={120} />
                </TableCell>
                <TableCell className="whitespace-normal break-words py-3">{propertyName(entry.property_id)}</TableCell>
                <TableCell className="py-3 capitalize">{entry.category ?? "—"}</TableCell>
                <TableCell className="py-3">
                  <StatusChip status={entry.status} tone={entry.status === "verified" ? "live" : "pending"} />
                </TableCell>
                <TableCell className="flex gap-2 py-3">
                  <Button variant="outline" size="sm" onClick={() => handleToggleVerified(entry.id, entry.status)}>
                    {entry.status === "verified" ? "Unverify" : "Verify"}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(entry.id)}>
                    Remove
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog
        open={!!answeringGap}
        onOpenChange={(open) => {
          if (!open) setAnsweringGap(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Answer — {answeringGap?.question}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleAnswerGap} className="space-y-4">
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
                  <Button type="button" variant="outline" size="sm" onClick={startRecording}>
                    {recordedBlob ? "Re-record" : "Start recording"}
                  </Button>
                ) : (
                  <Button type="button" variant="destructive" size="sm" onClick={stopRecording}>
                    Stop recording
                  </Button>
                )}
                {recordedUrl && <audio controls src={recordedUrl} className="h-8" />}
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

            <DialogFooter>
              <Button type="submit" disabled={answeringSubmitting || (!gapAnswerText && !recordedBlob)}>
                Save as FAQ entry
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
