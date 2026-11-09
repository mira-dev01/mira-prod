# MIRA host dashboard

The Next.js frontend for MIRA: hosts sign in here to see what Mira did on their guests' calls and to
control how she behaves. Backend and system overview: [`../CLAUDE.md`](../CLAUDE.md),
[`../docs/architecture.md`](../docs/architecture.md).

## Stack

Next.js 16 (App Router), React 19, TypeScript, Tailwind v4, shadcn/ui on `@base-ui/react`,
`lucide-react` icons, Clerk for sign-in. **This is not the Next.js you may know** — read the guide in
`node_modules/next/dist/docs/` before changing framework-level code ([`AGENTS.md`](AGENTS.md)).

## Run it

```bash
npm install
npm run dev      # http://localhost:3000
npm run build    # production build
npx tsc --noEmit # typecheck
npm run lint
```

| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Backend API root. **Must include the scheme and end in `/api/v1`.** Baked in at build time, so changing it needs a rebuild. |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | Clerk. |
| `NEXT_PUBLIC_CLERK_DEV_ORG_ID` | The Clerk dev org that unlocks internal-only features. |

**Signing in does not work on `localhost`.** Auth is a Clerk *development* instance whose allowed
origins are the Vercel URLs, so the real signed-in dashboard can only be used on a deployed Vercel URL
(see "Deployment" in [`../docs/architecture.md`](../docs/architecture.md)).

### Design preview mode (local UI work without signing in)

```bash
NEXT_PUBLIC_UI_PREVIEW=1 npm run dev -- -p 3100   # or the `frontend-preview` entry in ../.claude/launch.json
```

Opens `/dashboard/...` as a signed-in "HushStays" host with **invented sample data** — no backend, no
Clerk. It exists to see layout and styling changes; it proves nothing about real data. How it works:
`src/lib/ui-preview.ts` holds the fixtures and answers `GET` calls from `request()` in `src/lib/api.ts`
(anything without a fixture returns a 404, so that card shows its error state — add a fixture when a
page needs one; saves are accepted and ignored); `auth-context.tsx` pretends to be signed in and
`proxy.ts` skips Clerk. It is gated on `NODE_ENV !== "production"`, so it is compiled out of
`next build` and cannot be switched on in a deployed build.

If a CSS change doesn't show up in `npm run dev` even after a restart, delete the gitignored build
cache (`rm -rf .next`) and start again — the dev server can keep serving an old compiled stylesheet.

## Design system

Read [`../PRODUCT.md`](../PRODUCT.md) (who this is for) and [`../DESIGN.md`](../DESIGN.md) (tokens,
type, layout blocks, rules) before changing any UI.

- **Look:** "Visible Grid" — the original parchment/terracotta palette (the user's; do not change it)
  under a ruled 12-column layout, one grotesk (Schibsted Grotesk), flat hairline blocks, no shadows
  on the page.
- **Tokens and primitives:** `src/app/globals.css` (colours, type roles, the faint construction-grid
  utility `.field-grid`), `src/components/ui/{card,tabs,button,input,badge}.tsx`.
- **Layout blocks:** `src/components/settings/sheet.tsx` — `Sheet`, `Cell`, `WideCell`, `SheetRow`,
  `SheetHeading`, `CellFoot`; `segmented.tsx` for either/or choices. Blocks span whole columns and
  share ruled edges, so a row of blocks is always the same height.
- **Status:** only the Settings page (`src/app/dashboard/settings/`) is laid out in the new system.
  Every other page inherits the tokens and primitives but still needs its own pass, one page at a time.
- **Rule of thumb:** change design, layout, labels and copy — never remove a capability.

## Project map

```
src/app/dashboard/   per-domain pages (overview, leads, calls, properties, calendar, pricing, guests, settings, …)
src/components/      shared UI; ui/ = primitives; settings/ = Settings blocks and sections
src/hooks/           custom hooks (use-async, use-date-range, …)
src/lib/             API client (api.ts), auth context, capabilities/navigation contexts, types
```

## Deployment

Vercel, project `mira-prod`, root directory `frontend/`. See [`../docs/architecture.md`](../docs/architecture.md)
for the URLs and the `NEXT_PUBLIC_API_BASE_URL` pitfall.
