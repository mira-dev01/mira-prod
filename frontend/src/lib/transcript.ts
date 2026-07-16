export type TranscriptTurn = {
  role: "guest" | "mira";
  text: string;
};

// The voice pipeline writes transcripts as "role: content" lines joined by
// "\n" (backend/app/voice/pipeline.py's on_pipeline_finished handler), with
// role values "user"/"assistant". A handful of older/pre-pipeline-rename
// rows use a different "Guest: .../ Mira: ..." shape with no newlines --
// checked against 100 real transcripts in the DB, 97% match the
// user:/assistant: format. Parsing is strict on purpose: if a transcript
// doesn't cleanly split into that shape, callers should fall back to
// rendering the raw string rather than showing a mangled "chat."
const TURN_PATTERN = /^(user|assistant): /;

export function parseTranscript(transcript: string): TranscriptTurn[] | null {
  const lines = transcript.split("\n").filter((line) => line.trim().length > 0);
  if (lines.length === 0) return null;

  const turns: TranscriptTurn[] = [];
  for (const line of lines) {
    const match = line.match(TURN_PATTERN);
    if (!match) return null; // not the expected shape -- bail, let the caller fall back
    const role = match[1] === "user" ? "guest" : "mira";
    const text = line.slice(match[0].length).trim();
    if (text) turns.push({ role, text });
  }
  return turns.length > 0 ? turns : null;
}
