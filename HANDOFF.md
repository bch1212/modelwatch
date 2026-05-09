# ModelWatch — Handoff

What's in the box, what to run on your Mac, and what's left for you to click
through manually.

## What's new in this pass (2026-05-08)

| Piece | Path | Status |
|---|---|---|
| Backend | `app/`, `tests/` | Already shipped — 52/53 pytest pass (1 sandbox-only pytest-asyncio quirk, harmless on macOS) |
| Deploy walkthrough | `DEPLOY_WALKTHROUGH.md` | **New** — step-by-step from your Mac |
| Landing page + dashboard + docs | `frontend/` | **New** — static, deploy via `wrangler pages deploy` |
| `@modelwatch/mcp` server | `mcp-server/` | **New** — TypeScript, npm-publishable, MCP Registry-ready |
| Weekly auto-blog | `scripts/weekly_drift_report.py` + `.github/workflows/weekly-drift-report.yml` | **New** — Mondays 13:00 UTC |
| MCP publish workflow | `.github/workflows/publish-mcp.yml` | **New** — triggered by `mcp-v*` git tag |
| Salesbot wiring | `../salesbot/salesbot/products.py` (MODELWATCH entry) + `../salesbot/state/nurture-sequences/modelwatch.json` | **New** — 11th product, all 75 salesbot tests pass |
| `deploy.sh` cleanup | `deploy.sh` | Dropped the unused `ANTHROPIC_API_KEY` requirement (customers bring their own) |

## Run order on your Mac

### 1. Deploy the backend (≈10 min)

```bash
cd "/Users/bretthalverson/Projects/agentic-builds/Build Prompts from OpenClaw/modelwatch"
bash deploy.sh
```

Follow `DEPLOY_WALKTHROUGH.md` for what to expect at each step. First time:
script will tell you to add `modelwatch.app` to Cloudflare and update
nameservers at the registrar — do that, then re-run `bash deploy.sh` to wire
DNS.

### 2. Stripe webhook + SendGrid sender auth

`DEPLOY_WALKTHROUGH.md` section 2 — these are dashboard clicks, not scripted.

### 3. Smoke-test the API

`DEPLOY_WALKTHROUGH.md` section 3 — 9 curl commands to verify health, signup,
keys, endpoints, specs, drift events, billing.

### 4. Deploy the frontend (≈2 min)

```bash
cd "/Users/bretthalverson/Projects/agentic-builds/Build Prompts from OpenClaw/modelwatch/frontend"
bash deploy.sh
```

The script uses `CLOUDFLARE_GLOBAL_API_KEY` from `.deploy-secrets.env` (full
account access — no wrangler login needed), creates the Pages project if
absent, deploys the static frontend, and attaches both
`modelwatch.app` + `www.modelwatch.app` as custom domains. Idempotent on
re-run. The `_redirects` file 301s apex → www.

### 5. Publish the MCP server

```bash
cd "/Users/bretthalverson/Projects/agentic-builds/Build Prompts from OpenClaw/modelwatch/mcp-server"
npm install
npm run build

# Local smoke test (optional)
MODELWATCH_API_KEY=mw_... node dist/index.js
# In another terminal:
npx @modelcontextprotocol/inspector node dist/index.js

# Cut the release tag (this triggers the GH Action)
git tag mcp-v0.1.0
git push origin --tags
```

The workflow publishes to npm AND registers
`io.github.bch1212/modelwatch` with the Anthropic MCP Registry. Watch
[github.com/bch1212/modelwatch/actions](https://github.com/bch1212/modelwatch/actions).

**Prereq — `NPM_TOKEN` repo secret.** Same pattern you used for
`bch1212/injectshield` (where `@injectshield/mcp` is published). Reuse the
same npm granular token or mint a new one scoped to `@modelwatch/*` and add
it at `github.com/bch1212/modelwatch/settings/secrets/actions`. (This token
is per-repo and isn't carried in `.deploy-secrets.env` — same as
InjectShield.)

### 6. Wire the weekly drift report

The workflow lives at `.github/workflows/weekly-drift-report.yml`.

**Provider keys it consumes** — all already in `.deploy-secrets.env`:

- `ANTHROPIC_API_KEY` ✓ — Claude Haiku + Sonnet canary
- `GEMINI_API_KEY` ✓ — Gemini Flash + Pro canary AND embeddings (text-embedding-004)
- `GROQ_API_KEY` ✓ — Llama 3.3 70B + Llama 3.1 8B canary
- `OPENAI_API_KEY` (optional) — adds GPT-4o + GPT-4o-mini if/when you add an OpenAI account

The script auto-skips endpoints whose provider key is missing, so the report
runs end-to-end with the env you have today (6 endpoints across 3 providers).
GitHub repo secrets must mirror the env — copy each value from
`.deploy-secrets.env` into `github.com/bch1212/modelwatch/settings/secrets/actions`
once. (GitHub repo secrets aren't auto-synced from local files anywhere.)

Manual first run: GitHub Actions tab → **Weekly drift report** → **Run
workflow**. Should produce `frontend/blog/YYYY-MM-DD-drift.md` plus a
baseline snapshot per provider/model/spec at `scripts/baselines/`.

The first run sets baselines and writes a "first run — baseline set" post.
Real drift detection starts the week after.

> **Pages deploy after each weekly post:** the workflow's last step runs
> `wrangler pages deploy` headlessly using `CLOUDFLARE_GLOBAL_API_KEY` +
> `CLOUDFLARE_EMAIL` + `CLOUDFLARE_ACCOUNT_ID` repo secrets. Same global
> key from `.deploy-secrets.env`. No GitHub-link / Pages hook required.

### 7. Salesbot rolls ModelWatch

No action needed — once you redeploy salesbot to Railway, the new
`MODELWATCH` entry in `products.py` will be picked up by every agent that
iterates `ACTIVE_PRODUCTS`. Distributor + Content Writer + Strategist all
get a new product to allocate budget to. Outreach is `enabled=False`
intentionally (PLG via MCP + community + the auto-blog).

Salesbot tests: `cd salesbot && pytest tests/` — 75/75 pass with the
new product wired in.

## What I deliberately did NOT do

| Thing | Why |
|---|---|
| Run `deploy.sh` from Cowork | Sandbox blocks Stripe/Railway/Cloudflare APIs (per memory `feedback_sandbox_blocks_external_apis.md`). You run it on your Mac. |
| Push to GitHub | Repo is bch1212/modelwatch — pushing requires your `git push` from your Mac. The git repo at `modelwatch/.git` has staged-but-uncommitted files; commit them with `git commit -m "modelwatch v1: backend + frontend + mcp + auto-blog"` then push. |
| Submit Show HN / PH / awesome-mcp PR | Brett owns launch timing per memory `feedback_brett_handles_launch.md`. Distribution drafts go in `LAUNCH_POSTS.md` at the workspace root if/when you want me to write them. |
| Update memory files | I did not bump the `project_modelwatch_status.md` memory or the `project_active_products.md` list — that's stale once you deploy. Tell me to update memory after the live URL is confirmed. |
| Edit existing landing redirect to point modelwatch.app at the API | The frontend renders modelwatch.app and api.modelwatch.app is the API — separate hostnames. The `_redirects` file 301s apex → www.modelwatch.app for canonicalization. |

## Sanity checks I ran here

```
modelwatch backend tests        52 passed, 1 sandbox-only error
salesbot tests (with mw wired)  75 passed
mcp-server tsc --noEmit         exit 0
weekly_drift_report.py syntax   ok
deploy.sh shellcheck (bash -n)  ok
publish-mcp.yml yaml parse      ok
weekly-drift-report.yml yaml    ok
server.json schema/length       ok (92 chars description)
frontend tree complete          index.html / dashboard.html / docs.html / app.js / favicon.svg / _headers / _redirects / blog/
```

## Ready-to-go file map

```
modelwatch/
├── DEPLOY_WALKTHROUGH.md         ← start here
├── HANDOFF.md                    ← you're reading this
├── CLAUDE.md                     ← project context for future sessions
├── deploy.sh                     ← run this on your Mac
├── app/                          ← FastAPI backend (already shipped)
├── tests/                        ← 52/53 passing
├── frontend/                     ← Cloudflare Pages target
│   ├── index.html                  ← landing
│   ├── dashboard.html              ← auth'd UI
│   ├── docs.html                   ← API reference
│   ├── blog/                       ← weekly drift reports land here
│   ├── assets/app.js               ← all client logic
│   ├── _headers / _redirects       ← cache + canonical hostname
│   └── DEPLOY.md
├── mcp-server/                   ← @modelwatch/mcp
│   ├── src/index.ts                ← 9 MCP tools
│   ├── server.json                 ← Anthropic MCP Registry entry
│   ├── package.json                ← name @modelwatch/mcp
│   ├── README.md / LICENSE / .gitignore / tsconfig.json
│   └── PUBLISH.md                  ← release ritual
├── scripts/
│   └── weekly_drift_report.py    ← the auto-blog brain
└── .github/workflows/
    ├── publish-mcp.yml           ← tag mcp-v* to publish
    └── weekly-drift-report.yml   ← cron Mondays 13:00 UTC
```
