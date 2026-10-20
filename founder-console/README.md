# Founder Console

Internal-only API health & cost reference for MIRA. Deliberately a **separate app** from
`frontend/` (the host-facing dashboard) — nothing here is reachable from or linked out of the
host product, and it has its own passcode gate (`FOUNDER_PASSCODE`), not the host JWT auth.

## Running locally

```bash
cd founder-console
cp .env.example .env.local   # set FOUNDER_PASSCODE and BACKEND_BASE_URL
npm install
npm run dev   # http://localhost:4000
```

## What's real vs. estimated

- **LLM model health** (top section) is live — it calls the backend's existing
  `GET /api/v1/health/llm`.
- **External API cost reference** (bottom section) is a static planning table, not metered
  spend. MIRA doesn't currently log per-call token/second usage anywhere, so there's no real
  number to show yet. Wiring up real cost tracking means capturing usage at the call site for
  each provider (Groq token counts, Sarvam audio seconds, Exotel call minutes, Bright Data
  scrape counts) and persisting it — that's a backend change, not something this app can do on
  its own by polling.

## Deployment

Not in `render.yaml` — deploy separately (own Render service or wherever) if/when needed, same
Node build as `frontend/` (`npm install && npm run build`, start with `npm run start`).
