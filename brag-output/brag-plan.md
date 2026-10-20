# Brag Plan: MIRA

## What is this app?
MIRA is an AI voice receptionist for Airbnb/short-term-rental hosts in India — it answers every guest call instantly, 24/7, checks pricing and calendar, negotiates and qualifies the enquiry, then notifies the host on WhatsApp and logs a ready-to-close lead on the dashboard, all before the host even picks up the phone.

## The angle
The product's own landing page already storyboards its magic trick as a 7-step "guest call in real time" sequence (`CallFlowShowcase`) — ringing phone → Mira answers → checks the property → replies instantly → guest books → host gets pinged on WhatsApp → lead lands on the dashboard. The brag video is that exact sequence, compressed and paced like a night-shift you never had to work. The joke/claim isn't absurd — it's the quiet flex of watching a whole booking close itself while the host's phone never rings a second time. Hook on the ordinary dread of a late host call; resolve on the fact Mira already handled it.

## Hook (first 2-3 seconds)
Full-bleed warm cream screen, near-dark. A phone-ringing card fades up hard and fast: "Incoming call… +91 98765 43210" with a pulsing amber "ringing" dot. It reads like 11pm and a host's stomach dropping — except a small label underneath already says "Mira answering." The dread never gets to land.

## Key moments (the middle)
- Mira picks up and replies inside the call in real time: "Can I check in early, around 11am?" → card cross-fades straight to her instant answer: "Yes, 11am works — no extra charge."
- The guest confirms on the spot — a clean "Booking confirmed" card lands: "3 nights · 2 guests · Goa Villa," with the sage-green success dot from the product's own status system.
- The host never has to do anything — a WhatsApp bubble arrives: "Early check-in — approved by Mira," in the product's actual chart-2 WhatsApp-green accent.

## Outro / punchline
Cut to the dashboard: a new lead card slides into the list — "New enquiry · Goa Villa · Qualified" — while the hero's real headline sets in Libre Baskerville: "Stop answering calls. Start closing bookings." Hold on the wordmark (serif italic "M," matching the app's own favicon glyph) as the tagline "MIRA — AI Receptionist for Airbnb Hosts" settles underneath.

## User flow worth showing
Entry → key action → result, taken directly from `frontend/src/components/hero/call-flow-showcase.tsx`'s own 7-beat storyboard (already the product's chosen self-demo):
1. **Entry:** Guest calls at an inconvenient hour.
2. **Key action:** Mira answers immediately, looks up the property's calendar/house rules, and replies with a real answer — the guest confirms a booking on the call.
3. **Result:** The host is notified on WhatsApp with zero effort, and the qualified lead is already sitting on the dashboard.

## Tone
- Preset: polished
- Creative direction: a quiet, confident hospitality-tech film — boutique rather than corporate, matching the app's own serif-headline/warm-terracotta identity. Not a joke video; the "wow" is the product working while nobody's watching.
- Interpretation: fewer scenes, longer holds, restrained transitions (soft crossfade/slide). No hard cuts, no chaotic pacing, no gag beats. Confidence comes from letting each real product moment sit long enough to read, not from speed or noise.

## Format: landscape — 1920x1080
## Duration: 22-23s

## Visual identity (from the project)
- Background: `#f3ede2` (warm cream)
- Accent: `#b8452f` (terracotta/primary) and `#d9a441` (warm gold accent); `#75885f` (sage "live/success" status green) and WhatsApp-moment green `#6b7d5a` (`--chart-2`)
- Text: `#2a2420` (warm near-black)
- Display font: Libre Baskerville (serif — headline/wordmark)
- Body font: Montserrat (sans — labels, card body copy)
- Strongest visual element: the hero's own animated `CallFlowShowcase` card — a single floating card that cross-fades through the call's stages over a faint radial-glow + dot-grid backdrop, with a softly parallaxing blurred dashboard mockup behind it. Recreate this card system as the video's visual spine rather than inventing a new UI.

## Share copy (draft)
Your phone rings at 11pm. Mira answers, checks the calendar, and the guest books — before you even see the notification. Meet MIRA, the AI receptionist for Airbnb hosts.

## Audio direction
- Role: warm, restrained bed with a small number of motion-matched accents
- Music: `happy-beats-business-moves-vol-12-by-ende-dot-app.mp3` — "steady and clean," the library's pick for `polished`/`cinematic`
- Music treatment: fade in under the hook (0–0.6s), sit at 0.28-0.32 volume through the middle, gentle swell/no volume jump into the outro, fade out over the last ~0.8s after the final SFX hit
- Music cue guidance: preset read from `happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json` (tempo ~110 BPM). Target strong cues at **8.74s** (reveal transition into the mid-call highlights), **13.11s** (booking-confirmed beat), and **22.93s** (final logo/wordmark landing) — all within ±0.15s tolerance, never at the expense of a card's reading hold.
- Audio-reactive treatment: subtle — the radial glow behind the call card and the dashboard mockup's soft parallax may breathe slightly with RMS; no waveform/equalizer visuals.
- SFX posture: minimal but present (2-4 cues total) — one soft ring-adjacent tone on the hook, one gentle confirmation chime when Mira answers, one clean success accent on "Booking confirmed," one crisp notification ping on the WhatsApp beat. Nothing aggressive; polished restraint per `audio.md`'s tone table.
- Audio-coupled moments: each call-flow card's arrival gets its own single accent (not a per-word/per-character treatment) — this is a card-by-card sequence, not typed text.
- Restraint rule: no per-character typing sounds, no dense SFX stacking, no beat-synced flashing — this is a quiet product film, not a hype reel.

## Storyboard

### Scene 1 — Incoming — 3s
Dim warm-cream backdrop with the faint radial glow + dot-grid from the hero panel. A card slams up fast (0.2s) then holds: phone icon, pulsing gold "ringing" dot, "Incoming call…" / "+91 98765 43210." A micro-label ("MIRA — AI Receptionist for Airbnb Hosts") settles beneath at 1.6s and holds.
Sequential/interaction: none — single card, two-stage reveal (card, then label)
Audio intent: a hint of dread that gets defused almost immediately
Audio-coupled idea: a single soft ring-adjacent tone under the card's slam-in
Music: fade-in bed starts under this scene
Transition mood: soft crossfade → Scene 2

### Scene 2 — Mira answers — 5.5s
The ringing card cross-fades (soft) into the "answering" card: sage-green live dot, "Can I check in early, around 11am?" / "Goa Villa · guest enquiry." Holds ~2.5s, then crossfades into the "searching" card: blue progress dot, "Looking up check-in policy" / "Calendar + house rules · Goa Villa." Holds to end of scene.
Sequential/interaction: yes — two call-flow cards in sequence, each fully settled before the next arrives (this is the product's actual step order, not sped up)
Audio intent: quiet competence — the sense of a system that's already handling it
Audio-coupled idea: one gentle confirmation chime timed to the "answering" card's arrival only; no sound on "searching" (let the visual carry it)
Music: bed at full bed volume (~0.3), no swell yet
Transition mood: soft crossfade → Scene 3, timed near the 8.74s strong cue

### Scene 3 — The booking happens — 10s
Three sequential cards, each held to its full reading floor (~3.3s):
1. "Yes, 11am works — no extra charge." / "Mira replies instantly" (gold/progress accent)
2. "Guest confirms booking" / "3 nights · 2 guests · Goa Villa" (sage-green success accent — the emotional peak of the sequence)
3. "Host notified" / "Early check-in — approved by Mira" (WhatsApp-green chart-2 accent, a chat-bubble treatment rather than the generic card if it reads cleanly)
Sequential/interaction: yes — three cards in strict sequence, each fully settled (no overlap) before the next enters
Audio intent: card 1 is a quiet confirmation; card 2 is the payoff — a single clean success accent, not a fanfare; card 3 is a crisp, distinct notification ping (should read as "a phone buzzed," not "a bell rang")
Audio-coupled idea: success accent on card 2 targeted near the 13.11s strong cue; notification ping on card 3's arrival
Music: steady bed, no volume change
Transition mood: soft crossfade → Scene 4, timed near the 17.5-18.5s strong-cue pair

### Scene 4 — Outro — 4.5s
Cut wide to the dashboard mockup (no longer blurred/backgrounded — now the frame's subject): a new lead row slides in from the side — "New enquiry · Goa Villa · Qualified" — settles. Then the frame settles into the real hero headline in Libre Baskerville, "Stop answering calls. Start closing bookings.," with the serif-italic "M" wordmark and "MIRA — AI Receptionist for Airbnb Hosts" tagline landing last and holding to the end.
Sequential/interaction: yes — lead row slides in and settles, then headline/wordmark land as the final held frame
Audio intent: settled confidence, not a hype sting
Audio-coupled idea: one restrained accent on the wordmark's landing, targeted at the 22.93s strong cue
Music: gentle fade-out beginning after the wordmark accent, tail off by the last frame

**Music mood for this video:** polished / steady-and-clean, restrained throughout
**Audio summary:** A low, warm music bed carries the whole video; four small, motion-matched accents (ring, answer-chime, success beat, WhatsApp ping) mark the call's real turning points, and everything fades out under the held final wordmark rather than ending on a sting.
