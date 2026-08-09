"use client";

import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";

export type DictationStatus = "idle" | "recording" | "transcribing";

// Sarvam's real-time speech-to-text endpoint (client.speech_to_text.transcribe,
// used by faq_service.transcribe_gap_answer_audio) hard-caps clips at 30s and
// 400s anything longer ("Audio duration exceeds the maximum limit of 30
// seconds. Please use the batch API for longer audio files.") -- confirmed
// live via a BadRequestError out of the /voice/transcribe endpoint. Auto-stop
// the recording at that limit so a host who keeps talking gets a transcript
// of what they said instead of a failed request with nothing to show for it.
const MAX_RECORDING_MS = 30_000;

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
  const maxLengthTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
        if (maxLengthTimeoutRef.current) {
          clearTimeout(maxLengthTimeoutRef.current);
          maxLengthTimeoutRef.current = null;
        }
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
      maxLengthTimeoutRef.current = setTimeout(() => {
        if (mediaRecorderRef.current?.state === "recording") {
          toast.info("Stopped at 30s, the dictation limit -- transcribing what you said so far");
          mediaRecorderRef.current.stop();
        }
      }, MAX_RECORDING_MS);
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
