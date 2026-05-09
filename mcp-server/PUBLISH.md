# Publishing modelwatch-mcp

Two artifacts to publish on every release:

1. **npm package** (`modelwatch-mcp`) — what users install
2. **MCP Registry entry** (`io.github.bch1212/modelwatch`) — what makes the
   server discoverable in the Anthropic MCP Registry

The GitHub Action at `.github/workflows/publish-mcp.yml` does both in one
job, triggered by pushing a tag of the form `mcp-v*`.

## Prereq — one-time

1. **npm token**
   - npmjs.com → Profile → Access Tokens → **Generate Granular Token**
   - Scope: read & write packages, allow `@modelwatch/*`
   - Add to GitHub repo → Settings → Secrets → `NPM_TOKEN`
2. **GitHub repo claim** (per Anthropic MCP Registry rules from the
   `reference_mcp_registry_publish` memory)
   - Repo URL must be `github.com/bch1212/modelwatch`
   - The `mcpName` field in `package.json` (`io.github.bch1212/modelwatch`)
     is the ownership claim — npm publish carries it
3. **OIDC scope** — the workflow requests audience
   `https://registry.modelcontextprotocol.io` for `id-token: write`. No
   manual setup needed.

## Cut a release

Bump versions in two places (must match):

```bash
cd modelwatch/mcp-server

# 1. Bump npm version
npm version patch       # or minor/major

# 2. Sync server.json (matches package.json version exactly)
node -e 'const fs=require("fs"); const v=require("./package.json").version; const s=JSON.parse(fs.readFileSync("server.json","utf8")); s.version=v; s.packages[0].version=v; fs.writeFileSync("server.json", JSON.stringify(s,null,2)+"\n");'

# 3. Verify
git diff package.json server.json

# 4. Commit + tag
git add package.json server.json package-lock.json
git commit -m "mcp-server: v$(node -p "require('./package.json').version")"
git tag "mcp-v$(node -p "require('./package.json').version")"
git push origin main --tags
```

The tag push triggers `publish-mcp.yml`. Watch:
<https://github.com/bch1212/modelwatch/actions>

## Smoke-test before publishing

```bash
cd mcp-server
npm install
npm run build

# Run locally with your real key
MODELWATCH_API_KEY=mw_... node dist/index.js
# Then in another terminal use the @modelcontextprotocol/inspector to drive it:
npx @modelcontextprotocol/inspector node dist/index.js
```

## Common publish errors

These map to the three undocumented MCP Registry failure modes from the
shared `reference_mcp_registry_publish.md` memory:

| Error message | Fix |
|---|---|
| `invalid audience` from `/v0/auth/github-oidc` | Workflow's audience query param missing — already set in `publish-mcp.yml` step "Get GitHub Actions OIDC token" |
| `description must be ≤100 characters` | Trim `description` in `server.json` (currently 91 chars — leave room) |
| `mcpName ownership not claimed` | The `mcpName` field MUST be present in `package.json` AND the npm package MUST be published to npm before the registry publish runs (the workflow waits for npm to index — keep that step) |

## After successful publish

Verify:

```bash
# npm
curl -sS https://registry.npmjs.org/modelwatch-mcp | jq '."dist-tags".latest'

# MCP Registry
curl -sS "https://registry.modelcontextprotocol.io/v0/servers?search=modelwatch" | jq
```

Both should reflect the new version within ~30 seconds.
