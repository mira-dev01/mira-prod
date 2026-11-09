# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Property hosts running short-term rentals in India (villas, homestays, cottages), who sign in to the MIRA host dashboard. The person who set the account up uses it most; per the user, someone who has *not* set it up may also open it, so every label and section must make sense to a person with no setup history.

On the Settings page the job is to configure how Mira behaves for their guests: which number guests call, where live calls are handed to the host, when Mira answers versus the host, what the host is alerted about and how, plus features, billing and technicians.

## Product Purpose

MIRA is an AI assistant that answers guests' phone calls for a host, qualifies leads across the host's portfolio, manages bookings and the calendar, and handles pricing and negotiation inside rules the host sets. The dashboard is where the host sees what Mira did and controls how Mira behaves. Success for a settings surface: a host (or a colleague) can find a setting, understand what it does and what changes if they alter it, and finish, without help.

## Positioning

Mira picks up real guest calls and acts on the host's behalf under the host's own rules, so the dashboard has to make those rules legible and trustworthy, not just editable.

## Operating Context

- Hosts in India; the product handles calls in English and Hindi/Hinglish, but the dashboard itself is English only (confirmed).
- Telephony via Exotel, WhatsApp via Twilio, payments via Razorpay (host connects their own).
- Existing frontend: Next.js 16 (App Router), React 19, Tailwind v4, shadcn/ui on @base-ui/react, lucide icons. The Next.js version has breaking changes; read `node_modules/next/dist/docs/` before changing framework-level code (frontend/AGENTS.md).
- Clerk handles sign-in; localhost is not configured as a Clerk origin, so the deployed Vercel app (mira-prod-two.vercel.app) is the place the real signed-in UI can be seen.

## Capabilities and Constraints

- **Nothing may be removed.** Every existing Settings capability, field, toggle, tab and link must remain; this work changes design, layout, labelling and copy only (confirmed by the user).
- Settings tabs today: Your account, Features & modules, Dashboard, Technicians, Billing, API (coming soon), Team (coming soon). Account tab cards: profile, transfer numbers, host call hours, call intake number, notifications.
- Which settings are the highest priority to surface is **undecided** (the user chose no preference).

## Brand Commitments

- The product name is MIRA.
- Colour: keep the existing beige / parchment and terracotta palette (user, 2026-11-09: "keep the current beige terracotta color, not the blue version"). Do not change it.
- Type: the user disliked the old serif (Libre Baskerville) and Montserrat; the new neutral grotesk (Schibsted Grotesk) was accepted with the redesign.

## Evidence on Hand

- A working dashboard in `frontend/` with real data flows. No customer testimonials, usage figures or research are on hand, and none may be invented.

## Product Principles

1. **Nothing removed, only clarified**: redesign never deletes a capability; it makes it easier to find and understand.
2. **Legible to a stranger**: a first-time viewer who did not set the account up should understand each section's purpose from its label and a short line, without product history.
3. **Hierarchy over volume**: show what matters first, keep the rest available but quiet; information density comes from alignment and grouping, not from shrinking everything or stacking more boxes.
4. **Say what changes**: a setting explains the consequence for guests or the host, in plain words, not the implementation.

## Accessibility & Inclusion

No product-specific standard was established; follow WCAG AA contrast and keyboard operability as the baseline. Interface language is English only.
