"use client";

import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";

export type DictationStatus = "idle" | "recording" | "transcribing";

// Same record -> transcribe pattern as unanswered-questions-card.tsx's FAQ
// gap voice answer and the registration voice-intro flow (both MediaRecorder
// + a Sarvam-backed backend endpoint), generalized behind one hook so any
// text field's mic button records, stops, transcribes, and hands back plain
// text to insert -- no per-field recording/blob/audio-preview plumbing.
export function useDictation(onTranscribed: (text: string) => void) {
  const [status, setStatus] = useState<DictationStatus>("idle");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;

        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        setStatus("transcribing");
        try {
          const { text } = await api.voice.transcribe(blob);
          if (text) {
            onTranscribed(text);
          } else {
            toast.error("Could not make out any speech -- please try again or type it instead");
          }
        } catch (err) {
          toast.error(err instanceof ApiError ? err.message : "Transcription failed -- please try again");
        } finally {
          setStatus("idle");
        }
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setStatus("recording");
    } catch {
      toast.error("Could not access the microphone -- check browser permissions");
    }
  }, [onTranscribed]);

  const stop = useCallback(() => {
    mediaRecorderRef.current?.stop();
  }, []);

  const toggle = useCallback(() => {
    if (status === "recording") {
      stop();
    } else if (status === "idle") {
      start();
    }
  }, [status, start, stop]);

  return { status, start, stop, toggle };
}
