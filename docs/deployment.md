# Deployment, branches and CI

## Hosting
Backend on **Railway** (`backend/railway.json`: Dockerfile build, `/health` check, restart-on-failure) with **two environments, each with its own database and variables**: `dev` (Supabase Postgres, session-mode pooler) and `production` (Neon Postgres — the live system). Frontend on **Vercel**; Redis (`REDIS_URL` is set on both environments). Render is retired — `render.yaml` was deleted deliberately. Variable names and values differ between the environments (e.g. `production` has no `RESEND_API_KEY`, so host emails are skipped there): compare with `railway variables --environment dev|production` before relying on one for the other.

## Which branch deploys
- Railway **`dev`** environment ← the **`dev`** branch. A push runs `alembic upgrade head` against the Supabase DB on boot (confirmed 2026-11-09).
- Railway **`production`** environment ← **not recorded in the repo — fill in:** `______`

In GitHub protect whichever branch `production` deploys (require the `CI` check, no direct pushes, no force-push), and in Railway turn on **Wait for CI** for GitHub-triggered deployments so a failing CI run blocks the deploy.

## Branches as of 2026-11-09 (from git)
| Branch | Last commit | Relation to `dev` (your current branch) |
|---|---|---|
| `dev` | 2026-11-07 | newest; identical to `origin/dev` |
| `main` | 2026-10-27 (PR #58) | `dev` has 50 commits `main` lacks; `main` has 4 `dev` lacks |
| `abhaya`, `abhaya-dev`, `abhaya-latest`, `abhaya-new` | 2026-09-12 … 2026-10-27 | old; `dev` contains almost all of their work (they have 0–3 commits `dev` lacks) |
| `shagun` | 2026-10-26 | `dev` has 26 commits it lacks; it has none `dev` lacks |
| `hotfix/add-missing-prod-migrations-2` | 2026-10-10 | one-off migration fix |

So if production deploys from `main`, it is **50 commits behind `dev`** — including the access-control,
lease and failover fixes. Decide on one long-lived deploy branch, merge into it through pull requests
that pass CI, and delete or archive the stale `abhaya*` branches.

## CI (`.github/workflows/ci.yml`)
Runs on every push and pull request, about 3–5 minutes:
1. Installs from `backend/requirements.lock` (the same hash-verified set the Docker image installs).
2. Fails if `requirements.lock` is out of date with `requirements.txt`.
3. `ruff check .` using `backend/ruff.toml` (real-defect rules only: undefined names, syntax errors,
   redefinitions; unused imports are a separate cleanup).
4. `pytest -q` against Postgres 16 and Redis 7 service containers.

Tests are hermetic: `tests/conftest.py` rebuilds `settings` as if there were no `.env`, so a test run
never touches a real paid service and behaves the same on a laptop and in CI. The few tests that
deliberately call real APIs are marked `live_api` and only run with `RUN_LIVE_TESTS=1` (which also
keeps your `.env`).

## Dependencies
`requirements.txt` holds the version *ranges* we ask for; `requirements.lock` holds the exact, hashed
versions that ship. To change a dependency:
```bash
cd backend
# edit requirements.txt, then:
uv pip compile requirements.txt --universal --python-version 3.12 --generate-hashes -o requirements.lock
```
Commit both files; CI rejects a stale lock.

## Before the first deploy of these changes
- Set `TAKE_CALL_TOKEN_SECRET` (≥ 32 random bytes: `openssl rand -hex 32`) on **both Railway environments** — the app
  now refuses to start without it, and a push to `dev` auto-deploys the `dev` environment. Until it was required, an unset value meant "Take Call" links were signed with the
  public default `change-me`.
- Confirm `EXOTEL_WEBHOOK_TOKEN` is a real secret, and set `TWILIO_VOICE_WEBHOOK_TOKEN` /
  `TWILIO_WHATSAPP_WEBHOOK_TOKEN` if those integrations are in use (a placeholder now rejects every
  request in production).
- Check `FRONTEND_BASE_URL` equals the exact origin the dashboard runs on (Clerk token `azp` check).
- Size the database pool (`DB_POOL_SIZE` / `DB_MAX_OVERFLOW`, defaults 5 / 10 per process) so
  `(pool_size + max_overflow) × replicas` stays under the pooler/connection limit of the database that environment uses (Supabase pooler on `dev`; Neon's limit on `production`).

## Known gaps in the deploy itself (not yet changed)
- The Dockerfile `CMD` is `sh -c "alembic upgrade head && uvicorn …"` **without `exec`**: a deploy's SIGTERM reaches `sh`, not uvicorn, and Railway's default draining time is 0s, so a deploy can cut off live calls. Fix: `exec uvicorn …`, set `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` to the longest call (600+), and make the app stop accepting new calls on shutdown and wait for live ones. Migrations also run on every boot; a Railway pre-deploy command would run them once. Deploy outside peak call hours until this is done. (Plan: [event-loop-split-plan.md](event-loop-split-plan.md) Phase 6.)
- The container runs as root (no `USER` in the Dockerfile) — see [vapt-plan.md](vapt-plan.md) 0.9.
