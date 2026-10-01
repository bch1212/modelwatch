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

    assert package["version"] == "0.1.1"
    assert lock["version"] == package["version"]
    assert lock["packages"][""]["version"] == package["version"]
    assert package["version"] == manifest["version"]
    assert package["version"] == manifest["packages"][0]["version"]
    assert 'require("../package.json")' in source
    assert 'const PKG_VERSION = "' not in source