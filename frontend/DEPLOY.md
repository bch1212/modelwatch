# Frontend deploy — Cloudflare Pages

The frontend is a static site (no build step). Tailwind from CDN, vanilla JS,
ES5-safe.

## Deploy (one command)

```bash
cd "/Users/bretthalverson/Projects/agentic-builds/Build Prompts from OpenClaw/modelwatch/frontend"
bash deploy.sh
```

That script:

1. Loads `CLOUDFLARE_GLOBAL_API_KEY` from `../.deploy-secrets.env` (full
   account access — no wrangler OAuth needed).
2. Resolves your Cloudflare account ID.
3. Creates the `modelwatch-web` Pages project if it doesn't exist.
4. Deploys the static frontend as a Production deployment via wrangler
   (using global key + email env vars, headless).
5. Adds `modelwatch.app` and `www.modelwatch.app` as custom domains
   (idempotent).

Cloudflare email is read from `$CLOUDFLARE_EMAIL` env var; defaults to
`brett.halverson@gmail.com` (your CF account login).

## Why not the scoped CLOUDFLARE_TOKEN?

Per the `reference_cloudflare_pages_token` memory: the shared
`CLOUDFLARE_TOKEN` in `.deploy-secrets.env` is Zone:Read + DNS:Edit only and
**cannot** deploy Pages. The Global API Key is the universal credential
that bypasses that scope gap.

## Cache busting

`_headers` sets `max-age=300` for HTML and JS so dashboard fixes propagate
within 5 minutes. The `?v=20260508-1` query string on `app.js` in each HTML
file is the explicit version stamp — bump it on any deploy that changes JS
behaviour and old browsers pick it up immediately.

## API base URL

`assets/app.js` auto-detects:
- `localhost` / `127.0.0.1` → `http://localhost:8000`
- everything else → `https://api.modelwatch.app`

Override with `<script>window.MW_API_BASE='...'</script>` before `app.js`.

## CORS

The FastAPI backend sets `allow_origins=["*"]` so the Pages origins work
out of the box. If you ever lock that down, the Pages preview hostnames
look like `<hash>.modelwatch-web.pages.dev`.
