# Weekly call review

The aim: find out about bad calls without phoning in. Once a week, read the calls the scorecard flagged, work out
why they went wrong, and turn the most common cause into the next fix. This is a habit, not a feature — the tools
are already built (Task 15 of `docs/tasks/call-fluency-fixes.md`).

## Where to look

- **Founder console → Flagged calls** (`/admin/flagged`). Set the date range to the past week. The page shows how
  many calls were flagged, the most common reason, the three numbers below per host, and every flagged call with
  its reason. Open a call to read the transcript and play the recording.
- **Daily digest email** carries the same flagged count and top reason for the last 24 hours.
- To re-score a call from its stored rows (for example after changing a threshold), run
  `python -m scripts.rescore_call <call id>` or `--recent 20`. It only reads.

## What gets a call flagged

| Flag | Rule |
|---|---|
| Mira repeated the same question | The same question, even reworded, asked again two or more times (three asks in all) |
| Several guest turns were unclear audio | Two or more guest turns became `[inaudible]` |
| A tool failed | Any tool returned an error |
| Guest hung up right after Mira spoke | The guest ended the call within 10 s of Mira starting to speak |
| Mira was slow to answer | At least one reply started more than 3 s after the guest stopped |

A call from before scorecards existed cannot be flagged; the page says how many calls in range were scored.

## The weekly routine (about an hour)

1. Open Flagged calls for the past week. Write down the three numbers per host (below).
2. Read **every** flagged call.
3. Read **ten calls that were not flagged**. The page lists a random ten under the flagged calls (a new ten each load). This is how you find what the flags miss.
4. Give each failure **one** cause tag:
   - `asr` — the transcript was wrong or empty (speech-to-text, noise, language)
   - `search` — the right property was not found, or a place was named that is not on file
   - `prompt` — the model said the wrong thing or ignored a rule
   - `tool` — a tool failed or returned the wrong thing
   - `latency` — slow answers
   - `flow` — right words, wrong step (asked too much, closed too early, looped)
   - `other` — say what
5. The **most frequent cause** becomes the next fix.
6. Each reviewed failure becomes a scenario in `backend/eval/scenarios/regression_confirmed_live/` before its fix is
   merged (`docs/documentation/golden-eval-suite.md`).
7. Write the dated note (template below) in this folder, e.g. `weekly-call-review-2026-11-23.md`.

## The three numbers, per host, per week

Shown on the Flagged calls page, over scored calls:

1. **Percent of calls flagged.** Falling is good.
2. **Percent ending in a lead, booking or escalation.** Rising is good. (Whether an escalation was *correct* is a
   judgement; check it when you read the calls.)
3. **Percent where the guest hung up.** Watch it alongside the second number: a hang-up after a lead is fine.

## Note template

```
# Weekly call review — <date>

Week: <from> to <to>
Calls scored: <n>   Flagged: <n> (<pct>)   Ended in lead/booking/escalation: <pct>   Guest hung up: <pct>

## Flagged calls read
| Call | Host | Flag | Cause tag | What happened |

## Unflagged calls read (10)
| Call | Anything wrong? | Cause tag |

## Most frequent cause
<tag> — <count> calls. Next fix: <what>.

## Scenarios added
- <scenario file> — from call <id>
```

## Done when

After two weeks there are two dated notes in this folder and at least one eval scenario was added from each.
