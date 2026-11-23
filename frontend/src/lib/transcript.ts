import type { EscalationMarker } from "@/lib/types";

export type TranscriptTurn = {
  role: "guest" | "mira";
  text: string;
  // True for a guest turn the speech-to-text could not make out. The voice pipeline records it as the
  // marker "[inaudible]" (backend/app/voice/low_confidence_transcript_guard.py); it is not a quote.
  unclear?: boolean;
};

const INAUDIBLE_MARKER = "[inaudible]";

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
    if (text) turns.push(role === "guest" && text === INAUDIBLE_MARKER ? { role, text, unclear: true } : { role, text });
  }
  return turns.length > 0 ? turns : null;
}

// --- Where Mira escalated to the host --------------------------------------------------------------------------
//
// The saved transcript has no trace of the tool calls, so the backend keeps, for each escalation, the guest's line
// and Mira's reply (backend/app/services/transcript_markers.py). They are matched here by their text, in call order,
// not by position, because parseTranscript merges multi-line turns and positions would drift by one.

export type MarkedTurn = TranscriptTurn & { escalation?: boolean };

const squash = (text: string) => text.replace(/\s+/g, " ").trim().toLowerCase();

// What Mira says when she hands something to the host. Only used for a call that has no recorded markers (a call from
// before they were kept), so it is a best guess on Mira's own lines, never on the guest's.
const ESCALATION_WORDING =
  /\b(flag(ged|ging)?|escalat\w*|dispatch(ed|ing)?)\b|\b(let|inform|notify|notified|tell|told|alert|alerted|update)\b[^.?!]{0,30}\bhost\b|\bhost\b[^.?!]{0,40}\b(will|shall)\b[^.?!]{0,30}\b(call|reach|get back|contact|follow|check|confirm|arrange)\b|\btransfer(ring)?\b[^.?!]{0,20}\b(you|call|host)\b/i;

/** The turns, with the ones where Mira escalated to the host marked. `markers` null = not recorded for this call. */
export function markEscalations(turns: TranscriptTurn[], markers: EscalationMarker[] | null): { turns: MarkedTurn[]; recorded: boolean } {
  const marked: MarkedTurn[] = turns.map((turn) => ({ ...turn }));
  if (markers === null) {
    for (const turn of marked) if (turn.role === "mira" && ESCALATION_WORDING.test(turn.text)) turn.escalation = true;
    return { turns: marked, recorded: false };
  }
  let cursor = 0;
  for (const marker of markers) {
    const find = (role: MarkedTurn["role"], text: string | null, from: number) => {
      if (!text) return -1;
      const wanted = squash(text);
      return marked.findIndex((turn, i) => i >= from && turn.role === role && squash(turn.text) === wanted);
    };
    const guestAt = find("guest", marker.guest, cursor);
    if (guestAt >= 0) marked[guestAt].escalation = true;
    const miraAt = find("mira", marker.mira, guestAt >= 0 ? guestAt : cursor);
    if (miraAt >= 0) marked[miraAt].escalation = true;
    cursor = Math.max(cursor, miraAt >= 0 ? miraAt + 1 : guestAt >= 0 ? guestAt + 1 : cursor);
  }
  return { turns: marked, recorded: true };
}
