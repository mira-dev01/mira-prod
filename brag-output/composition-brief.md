# Hyperframes Composition Brief: MIRA

## Objective
Create a short launch-style brag video for MIRA, an AI voice receptionist for Airbnb/short-term-rental hosts in India.

## Output
- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 22-23 seconds

## Source Material
- Project root: `/Users/shagunverma/Desktop/mira /mira-prod`
- Primary files read: `frontend/src/app/page.tsx`, `frontend/src/components/hero/landing-hero.tsx`, `frontend/src/components/hero/call-flow-showcase.tsx`, `frontend/src/components/hero/dashboard-mockup.tsx`, `frontend/src/app/globals.css`, `frontend/src/app/layout.tsx`, `frontend/src/app/dashboard/page.tsx`
- Product name: MIRA
- Tagline / strongest claim: "Stop answering calls. Start closing bookings."
- Key UI or visual moment to recreate: the hero's `CallFlowShowcase` — a single floating card over a faint radial-glow + dot-grid backdrop, cross-fading through a guest call's real stages, with a softly parallaxing blurred dashboard mockup behind it
- Copy that must appear verbatim:
  - "Stop answering calls. Start closing bookings."
  - "MIRA — AI Receptionist for Airbnb Hosts"
  - "Can I check in early, around 11am?"
  - "Yes, 11am works — no extra charge."
  - "Guest confirms booking" / "3 nights · 2 guests · Goa Villa"
  - "Host notified" / "Early check-in — approved by Mira"
  - "New enquiry · Goa Villa · Qualified"

## Creative Direction
- Tone preset: polished
- Creative direction: a quiet, confident hospitality-tech film — boutique rather than corporate, matching the app's own serif-headline/warm-terracotta identity. The "wow" is the product working while nobody's watching, not a joke or a hype reel.
- Interpretation: fewer scenes, longer holds, restrained transitions (soft crossfade/slide), no hard cuts or chaotic pacing, no gag beats.
- Angle: The product's own landing page already storyboards its magic trick as a 7-step "guest call in real time" sequence. This video is that exact sequence, compressed and paced like the night-shift a host never had to work: hook on the ordinary dread of a late guest call, resolve on the fact Mira already handled it before the host even saw the notification.
- Hook: full-bleed warm cream screen, a phone-ringing card slams up fast then holds — "Incoming call… +91 98765 43210" with a pulsing ringing dot — reads like 11pm dread, except a small label underneath already says Mira's on it.
- Outro / punchline: dashboard cuts wide, a new qualified lead slides into the list, then the frame settles on the real hero headline in Libre Baskerville — "Stop answering calls. Start closing bookings." — with the serif-italic "M" wordmark and tagline landing last and holding.
- Avoid:
  - Generic SaaS language
  - Abstract filler visuals
  - Unrelated visual redesign
  - Hard cuts, chaotic pacing, or comedic gag beats (wrong tone for this project)

## Visual Identity
- Background: `#f3ede2` (warm cream)
- Text: `#2a2420` (warm near-black)
- Accent: `#b8452f` (terracotta/primary), `#d9a441` (warm gold, "pending"), `#75885f` (sage, "live"/success), `#5f7a99` (slate blue, "progress"), `#6b7d5a` (WhatsApp-moment green, `--chart-2`)
- Display font: Libre Baskerville (headline/wordmark) — Google Fonts, load `Libre+Baskerville:ital,wght@0,400;0,700;1,400`
- Body font: Montserrat (labels/card body copy) — Google Fonts, load `Montserrat:wght@400;500;600`
- Visual references from the project: the `CallFlowShowcase` card system (icon chip + micro-label + title + body, `rounded-2xl` card on a warm radial glow with a faint dot-grid), the status-dot color language (sage=live, gold=pending, blue=progress), the blurred `DashboardMockup` stat-tile/list silhouette, the hero's step-progress dots

## Storyboard
Use the storyboard in `brag-output/brag-plan.md` as the creative contract. Scene summary:
1. **Incoming** — 3s — ringing card slams in and holds ("Incoming call… +91 98765 43210"), micro-label settles beneath
2. **Mira answers** — 5.5s — answering card ("Can I check in early, around 11am?") crossfades to searching card ("Looking up check-in policy")
3. **The booking happens** — 10s — three sequential cards, each fully held: instant answer → booking confirmed (emotional peak) → WhatsApp host notification
4. **Outro** — 4.5s — dashboard cuts wide, new lead row slides in and settles, then real headline + wordmark + tagline land and hold to the final frame

## Audio
- Audio role: warm, restrained bed with a small number of motion-matched accents
- Audio arc: fades in under the hook, holds steady through the call sequence, one clean accent on the booking-confirmed peak, a distinct notification ping on the WhatsApp beat, one restrained accent on the final wordmark landing, then fades out under the held final frame
- Music: `happy-beats-business-moves-vol-12-by-ende-dot-app.mp3` (already copied to `brag-output/composition/assets/music/`)
- Music treatment: fade in 0–0.6s, bed volume ~0.28-0.32 through the middle, no volume spike into the outro, fade out over the last ~0.8s after the final SFX hit
- Music cue guidance: bundled preset at `<skill-dir>/assets/music/cues/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.{md,json}` (tempo ~110 BPM). Suggested strong-cue locks: ~8.74s (transition into the mid-call highlights), ~13.11s (booking-confirmed beat), ~22.93s (final wordmark landing) — treat as hints, not fixed points; skip any that hurt a card's reading hold.
- Audio-reactive treatment: subtle — the radial glow behind the call card and the dashboard mockup's soft parallax may breathe slightly with RMS. No waveform/equalizer visuals.
- Audio-coupled moments:
  - Scene 1 (ringing card slam-in) — a single soft ring-adjacent tone, restrained
  - Scene 2 (answering card arrival) — one gentle confirmation chime; no sound on the searching card
  - Scene 3 (booking-confirmed card) — one clean success accent, timed near the ~13.11s strong cue
  - Scene 3 (WhatsApp card) — a crisp, distinct notification ping (should read as "a phone buzzed," not a bell)
  - Scene 4 (wordmark landing) — one restrained accent, timed near the ~22.93s strong cue
- SFX selection guidance: card arrivals feel like `interface/drop_*` or `casino/card-place-*` (soft placement, not a slam); the booking-confirmed peak wants something clean and single like `impact/impactSoft_medium_*` or a soft `impact/impactBell_heavy_*`, not a fanfare; the WhatsApp ping should be crisp and small (`impact/impactGlass_light_*` or `impact/impactMetal_light_*` family reads more like a phone notification than a card sound); keep it to 4 total cues per the plan's restraint rule
- SFX analysis guidance: read `<skill-dir>/assets/sfx/sfx-analysis.md` and prefer low/medium high-frequency-risk files for these repeated, polished moments
- Exact SFX choice: Hyperframes should choose filenames, timestamps, density, and volume based on the implemented animation
- Audio files: copy the chosen music (already done) and any Hyperframes-selected SFX into `brag-output/composition/assets/`

## Hyperframes Instructions
Load the composition-building Hyperframes domain skills — `hyperframes-core` (composition contract + `data-*` timing), `hyperframes-animation` (motion), `hyperframes-creative` (design spec, beats, audio-reactive), `hyperframes-keyframes` (seek-safe keyframes), and `hyperframes-cli` (lint/check/render). `/brag` is its own workflow: do not enter the `hyperframes` entry-point intent interview and do not route into its generic promo / launch-video workflow. Prefer native Hyperframes conventions over anything in `/brag`.

Requirements:
- Show at least one real UI, copy, or visual element from the source project (the `CallFlowShowcase` card system, verbatim copy above).
- Keep all text readable in the final render.
- Keep the video within 15-25 seconds.
- Include the planned music/SFX layer.
- Treat `/brag` audio notes as guidance, not a fixed cue sheet. Choose SFX after the visual animation exists.
- Treat music cue metadata as optional timing hints. Ignore cues that hurt readability, scene pacing, or the product story.
- Major reveals may move toward nearby strong cues within about 0.15s. Smaller entrances may align to nearby beat points within about 0.10s. Use only 1-3 strong cue locks.
- Use SFX to support motion: card sounds for card-like reveals, a short announcement cue for the booking-confirmed payoff, restraint elsewhere.
- Honor the planned fade-in/fade-out and the no-volume-spike posture.
- Wire at least one visual element (radial glow or dashboard parallax) to subtle audio-reactive RMS.
- Use local assets for audio and any required runtime/media dependencies.
- Run `hyperframes check` before render — it is brag's single gate.
