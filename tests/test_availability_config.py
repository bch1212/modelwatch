import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_deploy_keeps_frontend_domains_on_pages():
    script = (ROOT / "deploy.sh").read_text()

    assert 'Authorization: Bearer $CLOUDFLARE_TOKEN' in script
    assert "Authorization: Bearer ***" not in script
    assert 'route["requiredValue"]' in script
    assert 'upsert_dns_record CNAME "api.$DOMAIN" "$RAILWAY_CNAME" false' in script
    assert 'upsert_dns_record TXT "$VERIFY_HOST" "$VERIFY_TOKEN" false' in script
    assert 'upsert_dns_record CNAME "$DOMAIN"' not in script
    assert 'upsert_dns_record CNAME "www.$DOMAIN"' not in script
    assert 'railway domain "$DOMAIN"' not in script
    assert 'railway domain "www.$DOMAIN"' not in script


def test_mcp_runtime_version_comes_from_package_metadata():
    package = json.loads((ROOT / "mcp-server" / "package.json").read_text())
    manifest = json.loads((ROOT / "mcp-server" / "server.json").read_text())
    source = (ROOT / "mcp-server" / "src" / "index.ts").read_text()

    assert package["version"] == manifest["version"]
    assert package["version"] == manifest["packages"][0]["version"]
    assert 'require("../package.json")' in source
    assert 'const PKG_VERSION = "' not in source