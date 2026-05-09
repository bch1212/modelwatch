#!/usr/bin/env bash
# Frontend deploy — Cloudflare Pages via API + global key.
#
# Why direct API instead of `wrangler pages deploy`:
#   - Wrangler defaults to OAuth (interactive login). The shared
#     CLOUDFLARE_GLOBAL_API_KEY in .deploy-secrets.env grants full account
#     access, so we can skip the login and deploy headlessly.
#   - The scoped CLOUDFLARE_TOKEN in .deploy-secrets.env is zone-only
#     (Zone:Read + DNS:Edit) and CANNOT deploy Pages — see memory
#     reference_cloudflare_pages_token. The global key avoids that gap.
#
# What it does:
#   1. Loads CLOUDFLARE_GLOBAL_API_KEY from ../.deploy-secrets.env
#   2. Resolves account_id (from CLOUDFLARE_ACCOUNT_ID env, else queries the
#      account list via global key)
#   3. Creates the Pages project if it doesn't exist
#   4. Uploads the static frontend as a new Production deployment
#
# Re-run-safe: project creation is idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_FILE="${REPO_ROOT}/../.deploy-secrets.env"
FRONTEND_DIR="${REPO_ROOT}/frontend"

PROJECT="modelwatch-web"
DOMAINS=("modelwatch.app" "www.modelwatch.app")

# Cloudflare account email — required for Global API Key auth.
# Override via CLOUDFLARE_EMAIL env var if your CF login isn't this address.
CLOUDFLARE_EMAIL="${CLOUDFLARE_EMAIL:-brett.halverson@gmail.com}"

if [[ ! -f "$SECRETS_FILE" ]]; then
  echo "ERROR: secrets file not found at $SECRETS_FILE" >&2
  exit 1
fi
set -a; source "$SECRETS_FILE"; set +a
: "${CLOUDFLARE_GLOBAL_API_KEY:?Missing CLOUDFLARE_GLOBAL_API_KEY in .deploy-secrets.env}"

CF_HEADERS=(
  -H "X-Auth-Email: ${CLOUDFLARE_EMAIL}"
  -H "X-Auth-Key: ${CLOUDFLARE_GLOBAL_API_KEY}"
  -H "Content-Type: application/json"
)

cf() { curl -sS "${CF_HEADERS[@]}" "$@"; }

# ---------------------------------------------------------------------------
# 1. Account ID
# ---------------------------------------------------------------------------
echo "--- 1/4: Resolve account ID ---"
if [[ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  ACCOUNT_ID="$CLOUDFLARE_ACCOUNT_ID"
  echo "[cf] Using CLOUDFLARE_ACCOUNT_ID from env: $ACCOUNT_ID"
else
  ACCOUNT_ID=$(cf "https://api.cloudflare.com/client/v4/accounts" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result'][0]['id'] if d.get('result') else '')")
  if [[ -z "$ACCOUNT_ID" ]]; then
    echo "ERROR: could not resolve account_id. Pass CLOUDFLARE_ACCOUNT_ID=... env var." >&2
    exit 1
  fi
  echo "[cf] Resolved account_id: $ACCOUNT_ID"
fi

# ---------------------------------------------------------------------------
# 2. Ensure project exists (idempotent)
# ---------------------------------------------------------------------------
echo
echo "--- 2/4: Pages project ---"
EXISTS=$(cf "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if d.get('success') else 'no')")
if [[ "$EXISTS" == "yes" ]]; then
  echo "[cf] Pages project '$PROJECT' already exists."
else
  echo "[cf] Creating Pages project '$PROJECT'..."
  cf -X POST "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects" \
    -d "{\"name\":\"$PROJECT\",\"production_branch\":\"main\"}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('[cf] created:', d.get('result',{}).get('id') or d)" || true
fi

# ---------------------------------------------------------------------------
# 3. Deploy via wrangler (uses the same global key headlessly)
# ---------------------------------------------------------------------------
echo
echo "--- 3/4: Deploy ---"
if ! command -v wrangler &>/dev/null; then
  echo "[cf] wrangler not found — installing globally (one-time)..."
  npm install -g wrangler@latest >/dev/null
fi

# Wrangler reads these env vars and skips OAuth login when both are set:
export CLOUDFLARE_API_KEY="$CLOUDFLARE_GLOBAL_API_KEY"
export CLOUDFLARE_EMAIL="$CLOUDFLARE_EMAIL"
export CLOUDFLARE_ACCOUNT_ID="$ACCOUNT_ID"

cd "$FRONTEND_DIR"
wrangler pages deploy . \
  --project-name="$PROJECT" \
  --branch=main \
  --commit-dirty=true

# ---------------------------------------------------------------------------
# 4. Custom domains (idempotent)
# ---------------------------------------------------------------------------
echo
echo "--- 4/4: Custom domains ---"
for D in "${DOMAINS[@]}"; do
  RESP=$(cf -X POST "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/$PROJECT/domains" \
    -d "{\"name\":\"$D\"}")
  OK=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if d.get('success') else d.get('errors',[{}])[0].get('message','?'))")
  echo "[cf] $D → $OK"
done

echo
echo "=== DONE ==="
echo "Live at:"
for D in "${DOMAINS[@]}"; do echo "  https://$D"; done
echo
echo "DNS: the api.modelwatch.app records were set by modelwatch/deploy.sh."
echo "For modelwatch.app + www.modelwatch.app, add CNAMEs pointing to"
echo "  $PROJECT.pages.dev"
echo "(or rely on Cloudflare Pages's auto-managed DNS once you Add the domains"
echo " above — Cloudflare adds the CNAMEs for you when the zone is in your account)."
