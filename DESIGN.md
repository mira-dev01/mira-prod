---
name: Mira Host Dashboard
description: "Visible Grid layout in the user's parchment and terracotta palette: ink hairline blocks on a faint 12-column construction grid."
colors:
  primary: "#b8452f"
  primary-foreground: "#ffffff"
  ink: "#2a2420"
  parchment: "#f3ede2"
  cream-paper: "#fbf7ef"
  muted-ink: "#6f6252"
  sand: "#ece3d4"
  soft-rule: "#e3d9c8"
  input-stroke: "#968a76"
  grid-line: "#e6dcc8"
  destructive: "#b8452f"
  status-live: "#5c7048"
  status-pending: "#d9a441"
  status-pending-strong: "#8a6512"
  status-progress: "#5f7a99"
  status-purple: "#8d6f96"
  status-orange: "#c17a42"
  accent-warm: "#d9a441"
  priority-low: "#7d7267"
typography:
  page-title:
    fontFamily: "Schibsted Grotesk, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.03em"
  block-heading:
    fontFamily: "Schibsted Grotesk, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: "1.75rem"
    letterSpacing: "-0.025em"
  row-name:
    fontFamily: "Schibsted Grotesk, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 700
    lineHeight: "1.25rem"
    letterSpacing: "0.1em"
  cell-name:
    fontFamily: "Schibsted Grotesk, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 700
    lineHeight: "1.25rem"
    letterSpacing: "0.1em"
  lead:
    fontFamily: "Schibsted Grotesk, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: "1.25rem"
  hint:
    fontFamily: "Schibsted Grotesk, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: "1.25rem"
  value:
    fontFamily: "Schibsted Grotesk, sans-serif"
    fontSize: "clamp(1.125rem, 1.5vw, 1.375rem)"
    fontWeight: 400
    lineHeight: "1.75rem"
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Schibsted Grotesk, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
  micro:
    fontFamily: "Schibsted Grotesk, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 700
    letterSpacing: "0.1em"
rounded:
  sm: "0.15rem"
  md: "0.2rem"
  base: "0.25rem"
spacing:
  cell: "20px"
  row-sm: "12px"
  control-gap: "16px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 14px"
  button-outline:
    backgroundColor: "{colors.cream-paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "36px"
  button-secondary:
    backgroundColor: "{colors.sand}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
  input:
    backgroundColor: "{colors.cream-paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "4px 12px"
  segmented-selected:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    rounded: "3px"
    height: "32px"
  badge:
    backgroundColor: "{colors.sand}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    height: "24px"
    padding: "2px 8px"
  cell:
    backgroundColor: "{colors.cream-paper}"
    textColor: "{colors.ink}"
    padding: "20px"
---

# Design System: Mira Host Dashboard

## Overview

**Creative North Star: "The Visible Grid"**

A parchment page ruled like a specimen sheet. A faint warm 12-column construction grid shows behind each Settings sheet, and every block snaps to whole columns with shared 1px ink borders and equal row heights. Nothing floats and nothing is a different size by accident. With the content removed it still reads as a pale warm column grid and square-cornered, hairline-ruled blocks.

Ink is a warm near-black on parchment, with lighter cream-paper blocks, and a single terracotta marks what is active, primary or selected. Sage, amber and red-brown appear only to report state. One neutral grotesk carries every role. Section names are small bold tracked caps; current values are large and plain.

**Key Characteristics:**
- 12-column grid with visible hairlines; blocks span whole columns.
- Ink 1px borders separate blocks; soft rules (`#e3d9c8`) separate rows inside a block.
- Flat at rest: no shadows on page content.
- 4px base radius, small and square-leaning.
- One typeface, hierarchy by weight, size and tracked caps.
- Terracotta means active/primary/selected only; state colors mean state only.

**Scope, honestly.** The Visible Grid was built and verified on the Settings, Overview, Leads, Calls and Properties surfaces (pages, `components/settings/*`, `components/overview/*`, the Leads tabs, `components/property-*.tsx`, `policies-tab.tsx`). The shared tokens (the user's parchment and terracotta colors, font, radius, shadows) and primitives (Button, Input, Badge, Card, Tabs, sidebar) are global, so other dashboard pages already inherit the new palette and font. The Sheet/Cell layout system, the construction grid, the type roles for headings and values, and the segmented control have NOT been applied to the remaining pages (Calendar, Pricing, Guests, FAQ, Analytics, Agent). Those pages have not been redesigned and may still use the older card layouts and spacing.

## Colors

A warm parchment-and-cream palette with one terracotta accent and a muted, earthy state set. **This palette is the user's own (the original parchment + terracotta, kept at their request after the blue version was rejected). It must not be changed or replaced when extending the system.**

### Primary
- **Terracotta** (`#b8452f`): active tab bar, primary button, selected segment, links and action links, focus ring, sidebar primary. The only accent. It is also the destructive color (same value), so destructive actions are told apart by label and tint (10% wash, red-brown text), never by hue.

### Neutral
- **Ink** (`#2a2420`): text, block borders, outline-button stroke, tab-list rule, sidebar border.
- **Parchment** (`#f3ede2`): page background.
- **Cream Paper** (`#fbf7ef`): cards, popovers, sidebar, inputs, and Sheet blocks (at 70% so the faint grid shows through); secondary button fill.
- **Muted Ink** (`#6f6252`): hints, inactive tabs, table heads, micro labels.
- **Sand** (`#ece3d4`): muted and accent fill; hover and active-nav backgrounds, disabled fields.
- **Soft Rule** (`#e3d9c8`): row dividers inside a block (the default `border` color).
- **Input Stroke** (`#968a76`): field borders, a deeper taupe so fields keep a 3:1 edge on cream.
- **Grid Line** (`#e6dcc8`): the construction hairlines; decorative only, never carries meaning.

### State (only for state)
- **Sage** (`#5c7048` text, rgba(117,136,95,.12) wash): live/ready. **Amber** (`#d9a441`; strong text variant `#8a6512`): pending/needs setup. Terracotta/red (`#b8452f`): blocked, invalid, destructive.
- Intent-category badge colors for calls/leads: progress slate-blue `#5f7a99`, purple `#8d6f96`, orange `#c17a42`. Low-priority tone `#7d7267`. These are state/category tints, not accents, and are not used as an accent on Settings.
- Chart series: `#d9a441`, `#6b7d5a`, `#c17a42`, `#5f7a99`, `#8d6f96`.
- **Brand mark amber** (`--accent-warm` `#d9a441`): the drawn star beside the wordmark.

### Named Rules
**The One Terracotta Rule.** Terracotta is the only accent: active tab, primary action, links and selection. Never use it for decoration, and never introduce a second accent.
**The State-Only Rule.** Sage, amber and red appear only where something reports status; each pairs with a label or dot, never color alone.
**The Palette Is Theirs Rule.** The parchment and terracotta palette is the user's decision; do not swap it for another.

## Typography

**Font:** Schibsted Grotesk (variable, `next/font`, `--font-grotesk`) with sans-serif fallback, for display, headings, body, labels and the "mira" wordmark. Monospace falls back to the system stack.

**Character:** a neutral grotesk; the weight axis does the work, so Tailwind medium/semibold/bold interpolate on one file.

### Hierarchy
- **Page title** (700, 28px, 1.15, -0.03em): one per page.
- **Block heading, level 1** (700, 20px, 28px line, tight, sentence case): parents a block that holds several rows (`SheetHeading`).
- **Row name** (700, 13px, tracked 0.1em, uppercase): names a `SheetRow`.
- **Cell name** (700, 12px, tracked 0.1em, uppercase): names a standalone `Cell` (`CellTitle`).
- **Lead** (500, 14px, 20px): one-line purpose of a block.
- **Hint** (400, 13px, 20px, muted): supporting detail; never smaller than 13px.
- **Value** (400, clamp 18px to 22px, 28px line, tight): Account values and key readouts.
- **Micro / table head** (700, 12px, tracked 0.08 to 0.1em, uppercase, muted): sidebar group labels, table column heads.

### Named Rules
**The Names-Small, Values-Large Rule.** Section names are small bold caps; the data they label is larger and plain. Names never compete with values.

## Layout

Settings, Overview, Leads, Calls and Properties content sits on `Sheet`: `grid grid-cols-12` with a top and left ink border, the 12-column hairline background (`background-size: calc(100% / 12)`, hidden below 768px where blocks stack). `Cell` (default full width, 20px padding, 12px gap, narrowed with `md:col-span-*`) and `WideCell` (subgrid, no padding, built from rows) close the grid with right and bottom ink borders, so neighbours share one rule and stretch to equal height. `SheetRow` puts name and hint in columns 1 to 4 (3 at md) and controls in columns 5 to 12 (4 to 12 at md). `CellFoot` pins fine print to a block's bottom behind a soft rule with a shared `min-h-[6.5rem]` at lg so rules align across a row. `SheetFooter` right-aligns actions on a soft rule. `CellCard` is a Cell that hosts Card parts (CardHeader/CardContent) for cards shared with other pages; `FieldRow` is a form row (the field name as a real `<label>` plus a one-line hint on the left, inputs on the right) and `SubField` one labelled input inside it; `ToggleField` is a switch line with a hint. The right-hand `RightPanel` uses the same ink rules and 20px bold title, so edit panels hold `Sheet` blocks directly. `Toolbar` is the full-width ruled strip above a list (search, filters, view switch, date picker). Lists inside a Cell use ruled rows (`ListRow` `boxed` = soft bottom rule, no box); stat cells are `col-span-6` on phones and `xl:col-span-3` or `xl:col-span-2` on desktop.

Spacing is Tailwind's 4px unit on even steps (2/4/6/8/10/12/16/20/24); odd steps only for optical correction. Main content padding is 16px, 24px at md. The sidebar is 224px (`w-56`) at md and up; below md it becomes a fixed 56px top bar plus a slide-in drawer.

### Named Rules
**The Whole-Column Rule.** A block spans whole columns; no free-floating widths on a Sheet.

## Elevation & Depth

Flat. Depth is conveyed by ink rules, not shadows. `[data-slot="card"]` forces `box-shadow: none`. Warm-tinted shadow tokens exist (`--shadow-sm` `0 1px 3px rgba(42,36,32,.10)` through `--shadow-xl`) and are reserved for layers that genuinely float: popovers, dialogs, drawers. Focus is a 2px terracotta outline with 2px offset.

### Named Rules
**The Flat-On-The-Page Rule.** Blocks on the page are ruled, never raised. Only floating layers get a shadow.

## Shapes

Small and square-leaning. Base radius 0.25rem (4px); derived sm 0.15rem, md 0.2rem. Buttons, inputs and the segmented control use `rounded-md`; badges `rounded-sm`; Sheet blocks have no radius (square, shared borders). Strokes are 1px; the active-tab mark is 4px.

## Components

### Buttons
- **Shape:** `rounded-md`, 36px high default (32 sm, 24 xs, 40 lg), 14px/600 text, no shadow.
- **Primary:** Terracotta fill, white text; hover darkens 14% toward black.
- **Outline:** cream paper, ink border; hover Sand. **Secondary:** cream paper with soft border. **Ghost** hover wash. **Destructive:** terracotta 10% tint, terracotta text. **Link:** terracotta, underline on hover.
- **Focus / press:** 2px terracotta outline offset 2px; active nudges 1px down. Disabled 50% opacity.

### Inputs / Fields
- 36px high (32 sm), `rounded-md`, 1px Input Stroke border, cream-paper fill, 14px at md.
- **Focus:** border and 2px ring in terracotta at 30% alpha. **Error:** terracotta border and 20% ring, 12px message below. **Disabled:** muted fill, 60% opacity.

### Cards / Containers
- Legacy `Card` is flat cream paper with a 1px ink border, `--radius`, 16px internal spacing; footer on a soft rule with muted fill. Within Settings, prefer Sheet/Cell over Card.

### Navigation
- **Tabs:** underline bar, 40px tall, 24px gaps, ink 1px rule beneath. Inactive tabs are muted 14px/500; active is ink 14px/700 with a 4px terracotta mark sitting on the rule. No pill backgrounds.
- **Sidebar:** cream paper, 1px right border, 224px, links with 16px lucide icons, 14px; active uses Sand and medium weight; groups use the micro label.

### Segmented control
A two or three-way selector drawn as one ink-bordered control (`rounded-md`, 2px inset); the selected option fills terracotta with white 13px/600 text, others are ink with a Sand hover; min height 32px.

### Status chips
Badge (24px, `rounded-sm`, 12px/600) with a 6px dot, label capitalized. Capability states map: Ready to sage, Needs setup to amber, Off/Available to neutral, Unavailable to slate. Requirement lines use a 6px dot (amber to do, grey optional, red blocked) with terracotta action links.

### Brand mark
Eight-point star drawn as a square-capped 2px stroke SVG in amber (`--accent-warm`), beside the lowercase bold "mira" wordmark (700, tracking -0.04em).

## Do's and Don'ts

### Do:
- **Do** build Settings content from `Sheet`, `Cell`, `WideCell`, `SheetRow`, `SheetHeading`, `CellFoot` so every edge lands on a column line.
- **Do** separate blocks with ink 1px borders and rows with soft rules.
- **Do** keep hints at 13px or larger and in muted ink; keep values larger than the names above them.
- **Do** reserve terracotta for the active tab, primary action, links and selection.
- **Do** use even spacing steps (4px unit).
- **Do** use `Segmented` for short either/or choices.

### Don't:
- **Don't** add shadows to blocks or cards on the page.
- **Don't** introduce a second typeface or a serif; the type is Schibsted Grotesk only (the earlier Libre Baskerville and Montserrat faces are retired, but the parchment and terracotta colors are kept).
- **Don't** change the palette or reintroduce blue as an accent; the user rejected the blue version.
- **Don't** use terracotta or the state colors decoratively, or state color without a label.
- **Don't** size blocks off the grid or let a row's blocks have unequal heights.
- **Don't** treat the `.dark` token block as live; the dashboard is always light.
- **Don't** assume other dashboard pages follow the Sheet system; they have not been redesigned.

## Not canonized (defects and drift the build carries)

- `.surface-interactive` (globals.css) lifts 2px and adds a shadow on hover. It contradicts the flat rule and is legacy on non-Settings pages; do not use it on Sheet blocks.
- The `.dark` token block is unused legacy (brighter terracotta `#d94f3d` on near-black); do not treat it as live.
- Sidebar links use `rounded-lg` and the nav `border-r` uses the soft border, off the 4px/ink-rule language; legacy drift.
- Brief vs build: the FIRST VIEWPORT brief says rail 192px and page title 34px; the build is 224px and 28px. The build is recorded.
- Favicon is a text glyph "M" data URI, not the Mira mark.
