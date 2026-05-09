# ModelWatch — Deploy Walkthrough

End-to-end deploy from your Mac. Follow top-to-bottom. Every step shows the
exact command and the expected output. If something looks different, jump to
the **Troubleshooting** section at the bottom.

> **Why your Mac and not from Cowork?** The Cowork sandbox blocks outbound
> calls to Stripe / Railway / Cloudflare APIs. `deploy.sh` provisions all three
> services in one pass and has to run somewhere with full network access.

---

## 0. Prereqs (one-time setup)

```bash
# Railway CLI
brew install railway

# Python deps deploy.sh leans on
pip3 install --user stripe cryptography
```

Verify the shared secrets file exists:

```bash
ls -la "/Users/bretthalverson/Projects/agentic-builds/Build Prompts from OpenClaw/.deploy-secrets.env"
```

If it's missing, stop here — every product in this workspace inherits secrets
from that file.

---

## 1. Run the deploy script

```bash
cd "/Users/bretthalverson/Projects/agentic-builds/Build Prompts from OpenClaw/modelwatch"
bash deploy.sh
```

What you should see, step by step:

### Step 1/6 — Encryption key
```
--- 1/6: Encryption key ---
[encryption] Generated new Fernet key (saved to .encryption_key — gitignored)
```
On a re-run: `[encryption] Reusing key from .encryption_key`. **Don't delete
this file** — every customer's stored LLM API key is encrypted with it.

### Step 2/6 — Stripe
```
--- 2/6: Stripe product + prices ---
[stripe] Created product: prod_XXXXXXXXXXXX
[stripe] Created price (pro): price_XXXXXXXXXXXX = $99.0/mo
[stripe] Created price (team): price_XXXXXXXXXXXX = $299.0/mo
[stripe] Created price (enterprise): price_XXXXXXXXXXXX = $999.0/mo
```
On a re-run: `Found existing product` / `Found existing price` for each line.
Lookup keys (`modelwatch_pro_monthly` etc.) deduplicate.

### Step 3/6 — Railway
First run prompts you to pick a workspace:
```
--- 3/6: Railway provisioning ---
[railway] Initializing project...
? Select a workspace
> Brett's Workspace
[railway] Attaching Postgres add-on...
[railway] Creating backend service with env vars...
```
Pick `Brett's Workspace` (or whichever workspace has the other products) and
hit Enter. Re-runs skip both `init` and `add` — `[railway] Already linked` /
`Postgres already attached`.

### Step 4/6 — Deploy
```
--- 4/6: Deploy ---
[railway] Triggering deploy to backend service...
Indexed
Compressed [====================] 100%
Build Logs: https://railway.com/project/.../service/.../build/...
[railway] Service is at: https://modelwatch-backend-production-XXXX.up.railway.app
```
The build takes 60-120 seconds. Note that `*.up.railway.app` URL — that's
your fallback before DNS goes live.

### Step 5/6 — Cloudflare DNS

**First-ever run** (zone not yet added to Cloudflare):
```
--- 5/6: Cloudflare DNS ---
[cloudflare] Zone modelwatch.app not yet in your Cloudflare account.
             Add it: https://dash.cloudflare.com → Add a Site → modelwatch.app
             Then update nameservers at your registrar...
```
Script exits cleanly. **API is live at the railway.app URL right now** — go
do the smoke tests in section 3, then come back here and add the zone:

1. Cloudflare → Add a Site → `modelwatch.app` (Free plan is fine)
2. Cloudflare assigns 2 nameservers (e.g. `cori.ns.cloudflare.com`)
3. Log into your registrar (wherever you bought modelwatch.app) and replace
   the nameservers with the 2 from Cloudflare
4. Wait for activation email (usually <1 hour)
5. Re-run `bash deploy.sh` — Step 5 will detect the zone and wire DNS

**Subsequent runs** (zone present):
```
[cloudflare] Zone ID: 1234567890abcdef
[cloudflare] Creating CNAME api.modelwatch.app → modelwatch-backend-production-XXXX.up.railway.app
[cloudflare] True
[cloudflare] Creating CNAME modelwatch.app → ...
[cloudflare] Creating CNAME www.modelwatch.app → ...
```

### Step 6/6 — Railway custom domain
```
--- 6/6: Railway custom domain ---
api.modelwatch.app added to backend
modelwatch.app added to backend
www.modelwatch.app added to backend
```

### Final summary
```
=== DONE ===

Endpoints:
  API:       https://api.modelwatch.app
  Web:       https://modelwatch.app
  Railway:   https://modelwatch-backend-production-XXXX.up.railway.app

Stripe webhook to register: ...
```

---

## 2. One-time manual steps after first deploy

### A. Stripe webhook
1. Stripe Dashboard → Developers → Webhooks → **Add endpoint**
2. URL: `https://api.modelwatch.app/api/billing/webhook`
   (Or the railway.app URL if DNS isn't live yet — you can update the
    endpoint URL later.)
3. Events to send:
   - `checkout.session.completed`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
4. After creating, click the endpoint → **Reveal signing secret**
5. Compare it to `STRIPE_WEBHOOK_SECRET` in `.deploy-secrets.env`. If different:
   ```bash
   railway variables --service backend --set "STRIPE_WEBHOOK_SECRET=whsec_NEW_VALUE_HERE"
   ```
   Wait ~30 seconds for Railway to redeploy with the new var.

### B. SendGrid sender authentication
SendGrid needs `alerts@modelwatch.app` to be authenticated or signup emails
will go to spam.

1. SendGrid Dashboard → Settings → Sender Authentication → **Authenticate
   Your Domain**
2. Domain: `modelwatch.app`. Use Cloudflare as DNS provider (auto-detect
   works). Click through.
3. SendGrid gives you 3 CNAME records. Add them in Cloudflare DNS (proxied
   OFF — gray cloud).
4. Back in SendGrid, click **Verify**. All 3 should turn green.

---

## 3. Smoke tests

Replace `BASE` with your real URL — railway.app pre-DNS, `api.modelwatch.app`
once DNS is live:

```bash
BASE=https://api.modelwatch.app
# or:
# BASE=https://modelwatch-backend-production-XXXX.up.railway.app

# 1. Health check
curl -s $BASE/health | jq .
# expected: {"status":"ok","service":"modelwatch"}

# 2. Signup → real email to your inbox
curl -s -X POST $BASE/api/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"brett.halverson@gmail.com","workspace_name":"Brett Smoke Test"}' \
  | jq .
# expected: 201, {"workspace_id": "...", "email": "...", "api_key": null}
#           (api_key is null because SendGrid is configured — check inbox)

# 3. Grab the mw_ key from the email and authenticate
KEY="mw_PASTE_FROM_EMAIL_HERE"
curl -s $BASE/api/workspaces/me -H "Authorization: Bearer $KEY" | jq .

# 4. Add your real OpenAI key (encrypted at rest)
curl -s -X POST $BASE/api/workspaces/me/api-keys \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"provider":"openai","api_key":"sk-PASTE_REAL_KEY"}' | jq .

# 5. Create an endpoint
EP=$(curl -s -X POST $BASE/api/endpoints \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"name":"GPT-4 prod","provider":"openai","model":"gpt-4o-mini"}' | jq -r .id)
echo "endpoint id: $EP"

# 6. Create a spec
SPEC=$(curl -s -X POST $BASE/api/specs \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "{\"endpoint_id\":\"$EP\",\"name\":\"Refusal canary\",\"prompt\":\"What is 2+2?\",\"frequency\":\"hourly\"}" | jq -r .id)
echo "spec id: $SPEC"

# 7. Manual run (sets the baseline first time)
curl -s -X POST $BASE/api/specs/$SPEC/run \
  -H "Authorization: Bearer $KEY" | jq .

# 8. Check dashboard
curl -s $BASE/api/dashboard/health -H "Authorization: Bearer $KEY" | jq .

# 9. Badge endpoint (public, no auth)
WS=$(curl -s $BASE/api/workspaces/me -H "Authorization: Bearer $KEY" | jq -r .id)
curl -s "$BASE/api/badge/$WS.svg" | head -c 200
```

If all 9 pass, you're live.

---

## 4. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `railway: command not found` | Railway CLI missing | `brew install railway` |
| `railway init` hangs after workspace pick | First-time auth | Open the URL it printed, log in, re-run `bash deploy.sh` |
| `Workspace still starting` from Railway | Postgres provisioning | Wait 30s and re-run; idempotent |
| `Build failed` in Railway logs | `requirements.txt` mismatch | `railway logs --service backend --build` to see; pin/repin in `requirements.txt` |
| `/health` returns 502 | App crashed at boot | `railway logs --service backend` — usually a missing env var |
| Signup returns 500 with "Could not connect to SMTP" | SendGrid sender domain not authenticated | See section 2.B above |
| Signup works but no email arrives | Sender not verified yet, or in spam | Check Spam folder; SendGrid → Activity Feed will show whether it sent |
| DNS step says "Zone not yet in your Cloudflare account" but you added it | Token scope misses Zone:Read | The shared `CLOUDFLARE_TOKEN` has Zone:Read + DNS:Edit — should work; double-check zone name is exactly `modelwatch.app` (no www., no http://) |
| `railway up` says "RAILWAY_TOKEN and RAILWAY_API_TOKEN both set" | Env conflict | `unset RAILWAY_TOKEN; export RAILWAY_API_TOKEN=$(grep RAILWAY_TOKEN .deploy-secrets.env \| cut -d= -f2)` then re-run |
| Tests fail with `OSError: could not get source code` | pytest-asyncio source caching glitch | Harmless in dev; doesn't affect prod. Clear with `find . -name __pycache__ -exec rm -rf {} +` |

### Force a redeploy
```bash
cd "/Users/bretthalverson/Projects/agentic-builds/Build Prompts from OpenClaw/modelwatch"
railway up --service backend --detach
```

### View live logs
```bash
railway logs --service backend
# or:
railway logs --service backend --deployment latest
```

### Tear down (keeps Postgres + Stripe artifacts)
```bash
railway down --service backend
```

### Rotate the encryption key (DESTRUCTIVE — invalidates all stored LLM keys)
```bash
rm .encryption_key
bash deploy.sh
# Then notify all customers their stored OpenAI/Anthropic keys must be re-added.
```

---

## 5. After deploy — what to do next

The backend is now live. Three more pieces ship to make ModelWatch a complete
product:

1. **Frontend** (`modelwatch/frontend/`) — Next.js landing + dashboard,
   deploys to Cloudflare Pages. See `frontend/DEPLOY.md`.
2. **MCP server** (`modelwatch/mcp-server/`) — `@modelwatch/mcp` for Claude
   Desktop / Claude Code. See `mcp-server/PUBLISH.md`.
3. **Auto-blog** — GitHub Action runs weekly, generates "did model X drift
   this week?" posts. See `.github/workflows/weekly-drift-report.yml`.

The salesbot is already configured to market ModelWatch (4th active product
alongside CastIQ + AgentFetch + GrantIQ).
