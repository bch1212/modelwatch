#!/usr/bin/env bash
# ModelWatch provisioning script — run from Brett's Mac with network access.
#
# What it does:
#   1. Creates Stripe product + 3 monthly prices (Pro $99, Team $299, Enterprise $999)
#   2. Initializes Railway project + attaches Postgres add-on
#   3. Sets all env vars on the backend service
#   4. Adds modelwatch.app DNS records at Cloudflare pointing to Railway
#   5. Triggers initial Railway deploy
#
# Idempotent — re-running is safe. Stripe lookup_keys + Railway service names
# act as natural dedup keys.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_FILE="${REPO_ROOT}/../.deploy-secrets.env"
export ENV_FILE="${REPO_ROOT}/.env.railway"
: > "$ENV_FILE"

PROJECT_NAME="modelwatch"
DOMAIN="modelwatch.app"

# ---------------------------------------------------------------------------
# Load shared secrets
# ---------------------------------------------------------------------------
if [[ ! -f "$SECRETS_FILE" ]]; then
  echo "ERROR: Secrets file not found at $SECRETS_FILE"
  exit 1
fi
set -a
source "$SECRETS_FILE"
set +a

: "${STRIPE_SECRET_KEY:?Missing STRIPE_SECRET_KEY}"
: "${SENDGRID_API_KEY:?Missing SENDGRID_API_KEY}"
: "${RAILWAY_TOKEN:?Missing RAILWAY_TOKEN}"
: "${CLOUDFLARE_TOKEN:?Missing CLOUDFLARE_TOKEN}"
: "${STRIPE_WEBHOOK_SECRET:?Missing STRIPE_WEBHOOK_SECRET}"
# ANTHROPIC_API_KEY intentionally not required — customers bring their own

# Per memory: account-scoped Railway token must be RAILWAY_API_TOKEN, and the
# CLI rejects requests when both vars are set.
export RAILWAY_API_TOKEN="$RAILWAY_TOKEN"
unset RAILWAY_TOKEN

BRETT_EMAIL="${BRETT_EMAIL:-brett.halverson@gmail.com}"

echo "=== ModelWatch provisioning ==="
echo "Project: $PROJECT_NAME"
echo "Domain:  $DOMAIN"
echo

# ---------------------------------------------------------------------------
# 1. Generate ENCRYPTION_KEY (per-deploy, persisted in .env.railway)
# ---------------------------------------------------------------------------
echo "--- 1/6: Encryption key ---"
ENCRYPTION_KEY_FILE="${REPO_ROOT}/.encryption_key"
if [[ -f "$ENCRYPTION_KEY_FILE" ]]; then
  ENCRYPTION_KEY=$(cat "$ENCRYPTION_KEY_FILE")
  echo "[encryption] Reusing key from .encryption_key"
else
  ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  echo "$ENCRYPTION_KEY" > "$ENCRYPTION_KEY_FILE"
  chmod 600 "$ENCRYPTION_KEY_FILE"
  echo "[encryption] Generated new Fernet key (saved to .encryption_key — gitignored)"
fi

# ---------------------------------------------------------------------------
# 2. Stripe — create product + prices
# ---------------------------------------------------------------------------
echo
echo "--- 2/6: Stripe product + prices ---"
python3 - <<'PYEOF'
import os
import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

# Find or create product (idempotent via metadata.slug)
products = stripe.Product.search(query="metadata['slug']:'modelwatch'").data
if products:
    product = products[0]
    print(f"[stripe] Found existing product: {product.id}")
else:
    product = stripe.Product.create(
        name="ModelWatch",
        description=(
            "Continuous behavioral drift monitoring for LLM-powered applications. "
            "Detects when LLM providers silently update their models in ways that "
            "break your application — before your customers notice."
        ),
        metadata={"slug": "modelwatch"},
    )
    print(f"[stripe] Created product: {product.id}")

tiers = [
    ("Pro",        9900,  "pro"),
    ("Team",       29900, "team"),
    ("Enterprise", 99900, "enterprise"),
]

price_ids = {}
for tier_name, amount, slug in tiers:
    lookup_key = f"modelwatch_{slug}_monthly"
    existing = stripe.Price.list(lookup_keys=[lookup_key], limit=1).data
    if existing:
        price = existing[0]
        print(f"[stripe] Found existing price ({slug}): {price.id}")
    else:
        price = stripe.Price.create(
            product=product.id,
            unit_amount=amount,
            currency="usd",
            recurring={"interval": "month"},
            nickname=f"ModelWatch {tier_name} Monthly",
            lookup_key=lookup_key,
            metadata={"tier": slug},
        )
        print(f"[stripe] Created price ({slug}): {price.id} = ${amount/100}/mo")
    price_ids[slug] = price.id

with open(os.environ["ENV_FILE"], "a") as f:
    f.write(f"STRIPE_PRICE_PRO={price_ids['pro']}\n")
    f.write(f"STRIPE_PRICE_TEAM={price_ids['team']}\n")
    f.write(f"STRIPE_PRICE_ENTERPRISE={price_ids['enterprise']}\n")
PYEOF

# ---------------------------------------------------------------------------
# 3. Railway — init project, attach Postgres, set env vars, deploy
# ---------------------------------------------------------------------------
echo
echo "--- 3/6: Railway provisioning ---"
if ! command -v railway &>/dev/null; then
  echo "ERROR: Railway CLI not installed. Run: brew install railway"
  exit 1
fi

cd "$REPO_ROOT"

if [[ ! -d .railway ]] && [[ ! -f .railway/project.json ]]; then
  echo "[railway] Initializing project..."
  railway init --name "$PROJECT_NAME"
else
  echo "[railway] Already linked to a project"
fi

# Postgres add-on
if ! railway variables --kv --service Postgres 2>/dev/null | grep -q "^DATABASE_URL="; then
  echo "[railway] Attaching Postgres add-on..."
  railway add --database postgres
else
  echo "[railway] Postgres already attached"
fi

# Load Stripe price IDs from previous step
source "$ENV_FILE"

API_BASE="https://api.$DOMAIN"
WEB_BASE="https://www.$DOMAIN"
CORS_ALLOWED="https://$DOMAIN,https://www.$DOMAIN"

# Create the FastAPI 'backend' service WITH all variables in one shot.
# Per RegImpact pattern: --service backend with --variables flags creates the
# service and lands the vars on it (not on Postgres).
if ! railway variables --kv --service backend 2>/dev/null | grep -q "^ENCRYPTION_KEY="; then
  echo "[railway] Creating backend service with env vars..."
  railway add \
    --service backend \
    --variables "ENCRYPTION_KEY=$ENCRYPTION_KEY" \
    --variables "STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY" \
    --variables "STRIPE_WEBHOOK_SECRET=$STRIPE_WEBHOOK_SECRET" \
    --variables "STRIPE_PRICE_PRO=$STRIPE_PRICE_PRO" \
    --variables "STRIPE_PRICE_TEAM=$STRIPE_PRICE_TEAM" \
    --variables "STRIPE_PRICE_ENTERPRISE=$STRIPE_PRICE_ENTERPRISE" \
    --variables "SENDGRID_API_KEY=$SENDGRID_API_KEY" \
    --variables "SENDGRID_FROM_EMAIL=alerts@$DOMAIN" \
    --variables "DEBUG=false" \
    --variables 'DATABASE_URL=${{Postgres.DATABASE_URL}}'
else
  echo "[railway] backend service exists; updating env vars..."
  railway variables --service backend \
    --set "ENCRYPTION_KEY=$ENCRYPTION_KEY" \
    --set "STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY" \
    --set "STRIPE_WEBHOOK_SECRET=$STRIPE_WEBHOOK_SECRET" \
    --set "STRIPE_PRICE_PRO=$STRIPE_PRICE_PRO" \
    --set "STRIPE_PRICE_TEAM=$STRIPE_PRICE_TEAM" \
    --set "STRIPE_PRICE_ENTERPRISE=$STRIPE_PRICE_ENTERPRISE" \
    --set "SENDGRID_API_KEY=$SENDGRID_API_KEY" \
    --set "SENDGRID_FROM_EMAIL=alerts@$DOMAIN" \
    --set "DEBUG=false" \
    --set 'DATABASE_URL=${{Postgres.DATABASE_URL}}'
fi

# ---------------------------------------------------------------------------
# 4. Deploy
# ---------------------------------------------------------------------------
echo
echo "--- 4/6: Deploy ---"
echo "[railway] Triggering deploy to backend service..."
railway up --service backend --detach

# Get the auto-generated public domain (always service-scoped to backend)
RAILWAY_DOMAIN=$(railway domain --service backend 2>/dev/null | grep -o '[a-z0-9-]*\.up\.railway\.app' | head -1)
if [[ -z "$RAILWAY_DOMAIN" ]]; then
  echo "[railway] Generating public domain..."
  railway domain --service backend
  RAILWAY_DOMAIN=$(railway domain --service backend 2>/dev/null | grep -o '[a-z0-9-]*\.up\.railway\.app' | head -1)
fi
echo "[railway] Service is at: https://$RAILWAY_DOMAIN"

# ---------------------------------------------------------------------------
# 5. Cloudflare DNS — api.modelwatch.app + apex/www → Railway
# ---------------------------------------------------------------------------
echo
echo "--- 5/6: Cloudflare DNS ---"

ZONE_RESP=$(curl -sS -X GET "https://api.cloudflare.com/client/v4/zones?name=$DOMAIN" \
  -H "Authorization: Bearer $CLOUDFLARE_TOKEN" \
  -H "Content-Type: application/json")
ZONE_ID=$(echo "$ZONE_RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['result'][0]['id'] if d.get('result') else '')")

if [[ -z "$ZONE_ID" ]]; then
  echo "[cloudflare] Zone $DOMAIN not yet in your Cloudflare account."
  echo "             Add it: https://dash.cloudflare.com → Add a Site → $DOMAIN"
  echo "             Then update nameservers at your registrar (where you bought $DOMAIN)."
  echo "             Then re-run this script — it will pick up the new zone."
  echo
  echo "Until DNS is wired up, the API is live at: https://$RAILWAY_DOMAIN"
  echo "You can smoke-test the deploy now: curl https://$RAILWAY_DOMAIN/health"
  exit 0
fi
echo "[cloudflare] Zone ID: $ZONE_ID"

RAILWAY_HOST="${RAILWAY_DOMAIN#https://}"
RAILWAY_HOST="${RAILWAY_HOST%%/*}"

# Idempotent DNS upsert helper
upsert_cname() {
  local name="$1"   # full hostname
  local target="$2" # CNAME target

  # Look up existing record
  EXISTING=$(curl -sS -X GET "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?type=CNAME&name=$name" \
    -H "Authorization: Bearer $CLOUDFLARE_TOKEN")
  REC_ID=$(echo "$EXISTING" | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('result') or []; print(r[0]['id'] if r else '')")

  if [[ -n "$REC_ID" ]]; then
    echo "[cloudflare] Updating CNAME $name → $target"
    curl -sS -X PATCH "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$REC_ID" \
      -H "Authorization: Bearer $CLOUDFLARE_TOKEN" \
      -H "Content-Type: application/json" \
      --data "{\"type\":\"CNAME\",\"name\":\"$name\",\"content\":\"$target\",\"proxied\":true}" \
      | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'[cloudflare] {d.get(\"success\")}')" || true
  else
    echo "[cloudflare] Creating CNAME $name → $target"
    curl -sS -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
      -H "Authorization: Bearer $CLOUDFLARE_TOKEN" \
      -H "Content-Type: application/json" \
      --data "{\"type\":\"CNAME\",\"name\":\"$name\",\"content\":\"$target\",\"proxied\":true}" \
      | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'[cloudflare] {d.get(\"success\")}')" || true
  fi
}

upsert_cname "api.$DOMAIN" "$RAILWAY_HOST"
upsert_cname "$DOMAIN"     "$RAILWAY_HOST"  # apex (CF auto-flattens)
upsert_cname "www.$DOMAIN" "$RAILWAY_HOST"

# ---------------------------------------------------------------------------
# 6. Railway custom domain
# ---------------------------------------------------------------------------
echo
echo "--- 6/6: Railway custom domain ---"
railway domain "api.$DOMAIN" --service backend 2>/dev/null || echo "[railway] api.$DOMAIN may already be set"
railway domain "$DOMAIN"     --service backend 2>/dev/null || true
railway domain "www.$DOMAIN" --service backend 2>/dev/null || true

# ---------------------------------------------------------------------------
echo
echo "=== DONE ==="
echo
echo "Endpoints:"
echo "  API:       https://api.$DOMAIN"
echo "  Web:       https://$DOMAIN  (until frontend ships, redirects to API docs)"
echo "  Railway:   https://$RAILWAY_DOMAIN  (always available)"
echo
echo "Stripe webhook to register:"
echo "  URL:    https://api.$DOMAIN/api/billing/webhook"
echo "  Events: checkout.session.completed,"
echo "          customer.subscription.deleted,"
echo "          invoice.payment_failed"
echo "  (Already-set STRIPE_WEBHOOK_SECRET should match the new endpoint's signing secret —"
echo "   if not, update it in the Stripe Dashboard and via 'railway variables --set'.)"
echo
echo "Smoke test:"
echo "  curl https://api.$DOMAIN/health"
echo "  curl -X POST https://api.$DOMAIN/api/auth/signup \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"email\":\"$BRETT_EMAIL\",\"workspace_name\":\"Brett\"}'"
