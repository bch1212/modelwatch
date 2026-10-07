import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_deploy_keeps_frontend_domains_on_pages():
    script = (ROOT / "deploy.sh").read_text()
    helper = (ROOT / "scripts" / "deploy_dns.py").read_text()

    assert 'python3 "$REPO_ROOT/scripts/deploy_dns.py" cloudflare' in script
    assert '--cname "$RAILWAY_CNAME"' in script
    assert 'RAILWAY_ENVIRONMENT="production"' in script
    assert 'railway environment link "$RAILWAY_ENVIRONMENT"' in script
    assert '--environment "$RAILWAY_ENVIRONMENT"' in script
    assert 'record.get("recordType") == "DNS_RECORD_TYPE_CNAME"' in helper
    assert 'cname = cname_records[0].get("requiredValue")' in helper
    assert 'f"api.{args.domain}"' in helper
    assert 'match_content=True' in helper
    assert 'f"{args.domain}"' not in helper
    assert 'f"www.{args.domain}"' not in helper
    assert 'railway domain "$DOMAIN"' not in script
    assert 'railway domain "www.$DOMAIN"' not in script


def test_mcp_runtime_version_comes_from_package_metadata():
    package = json.loads((ROOT / "mcp-server" / "package.json").read_text())
    lock = json.loads((ROOT / "mcp-server" / "package-lock.json").read_text())
    manifest = json.loads((ROOT / "mcp-server" / "server.json").read_text())
    source = (ROOT / "mcp-server" / "src" / "index.ts").read_text()

    assert package["version"] == "0.1.3"
    assert lock["version"] == package["version"]
    assert lock["packages"][""]["version"] == package["version"]
    assert package["version"] == manifest["version"]
    assert package["version"] == manifest["packages"][0]["version"]
    assert 'require("../package.json")' in source
    assert 'const PKG_VERSION = "' not in source


def test_mcp_publish_workflow_uses_compatible_tools_and_waits_for_exact_version():
    workflow = (ROOT / ".github" / "workflows" / "publish-mcp.yml").read_text()

    assert 'node-version: "22.22.2"' in workflow
    assert "npm install -g npm@" not in workflow
    assert 'npm install --prefix "$RUNNER_TEMP/npm-cli" --no-save --ignore-scripts npm@12.2.0' in workflow
    assert 'PINNED_NPM="$RUNNER_TEMP/npm-cli/node_modules/npm/bin/npm-cli.js"' in workflow
    assert '"$PINNED_NPM" publish --provenance --access public' in workflow
    assert "expected=$(node -p \"require('./package.json').version\")" in workflow
    assert 'if [ "$ver" = "$expected" ]; then' in workflow
    assert "npm did not index version $expected" in workflow
    assert "continuing anyway" not in workflow