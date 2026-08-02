export type TranscriptTurn = {
  role: "guest" | "mira";
  text: string;
};

// The voice pipeline writes transcripts as "role: content" lines joined by
// "\n" (backend/app/voice/pipeline.py's on_pipeline_finished handler), with
// role values "user"/"assistant". A single turn's own content can itself
// contain "\n" (e.g. a multi-option assistant reply with a paragraph break),
// which produces extra lines with no "role: " prefix -- those are
// continuations of the previous turn, not new turns, and get appended back
// onto it rather than treated as a parse failure. A handful of older/
// pre-pipeline-rename rows use a different "Guest: .../ Mira: ..." shape --
// checked against 100 real transcripts in the DB, 97% match the
// user:/assistant: format. Only bail to the caller's raw-string fallback if
// the very first line isn't a recognized turn -- otherwise there's nothing
// to anchor an unprefixed line to.
const TURN_PATTERN = /^(user|assistant): /;

export function parseTranscript(transcript: string): TranscriptTurn[] | null {
  const lines = transcript.split("\n").filter((line) => line.trim().length > 0);
  if (lines.length === 0) return null;

  const turns: TranscriptTurn[] = [];
  for (const line of lines) {
    const match = line.match(TURN_PATTERN);
    if (!match) {
      const previous = turns[turns.length - 1];
      if (!previous) return null; // no turn yet to attach this continuation line to
      previous.text = `${previous.text}\n${line.trim()}`;
      continue;
    }
    const role = match[1] === "user" ? "guest" : "mira";
    const text = line.slice(match[0].length).trim();
    if (text) turns.push({ role, text });
  }
  return turns.length > 0 ? turns : null;
}
