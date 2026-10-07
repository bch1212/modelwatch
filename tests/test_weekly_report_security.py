"""Regression coverage for public-report injection and provider-key logging."""

import asyncio
from datetime import date

import httpx
import pytest

from scripts import weekly_drift_report as report


@pytest.mark.asyncio
async def test_gemini_credentials_are_sent_in_headers_not_urls(monkeypatch):
    secret = "synthetic-gemini-key-for-test-only"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    requests = []

    def respond(request):
        requests.append(request)
        if "embedContent" in request.url.path:
            return httpx.Response(200, json={"embedding": {"values": [0.2, 0.4]}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    assert await report.call_gemini("gemini-2.5-flash", "hello") == "ok"
    assert await report.embed("hello") == [0.2, 0.4]
    assert len(requests) == 2
    for req in requests:
        assert secret not in str(req.url)
        assert req.headers["x-goog-api-key"] == secret


@pytest.mark.asyncio
async def test_gemini_failure_never_prints_secret(monkeypatch, capsys):
    secret = "synthetic-gemini-key-for-test-only"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    original = httpx.AsyncClient

    def reject(request):
        return httpx.Response(429, request=request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(reject), **kw))
    assert await report.embed("hello") is None
    assert secret not in capsys.readouterr().err
    assert secret not in report.safe_error(ValueError("provider echoed " + secret))


def test_fenced_model_output_cannot_escape_to_markdown_html():
    malicious = "```\n<div><img src=x onerror=alert(1)></div>\n</script>"
    block = report.code_block(malicious)
    assert block[0] == block[-1]
    assert len(block[0]) > 3
    assert block[1] == malicious
    post = report.render_post(date(2026, 10, 7), [{
        "severity": "high", "provider": "gemini", "model": "gemini-test",
        "spec_name": "test spec", "drift_score": 0.9,
        "baseline_output": malicious, "current_output": malicious,
    }])
    assert post.count(block[0]) == 4
    assert post.count("</script>") == 2  # Source text stays inside the fences.


@pytest.mark.asyncio
async def test_generated_html_encodes_script_closing_from_provider(monkeypatch, tmp_path):
    blog = tmp_path / "blog"
    blog.mkdir()
    monkeypatch.setattr(report, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(report, "BLOG_DIR", blog)
    monkeypatch.setattr(report, "INDEX_PATH", blog / "index.json")
    monkeypatch.setattr(report, "active_endpoints", lambda: [{"provider": "gemini", "model": "test"}])
    monkeypatch.setattr(report, "CANARY_SPECS", [{"id": "test", "name": "test", "prompt": "test"}])
    monkeypatch.setattr(report, "PROVIDER_PACING", {"gemini": 0})
    monkeypatch.setattr(report, "load_baseline", lambda *a: {"output": "before"})
    monkeypatch.setattr(report, "save_baseline", lambda *a: None)
    monkeypatch.setattr(report, "embed", lambda *a: asyncio.sleep(0, result=None))
    monkeypatch.setattr(report, "call_endpoint", lambda *a: asyncio.sleep(0, result="```\n</script><img src=x onerror=alert(1)>"))

    class Diff:
        drift_score = 0.9
        format_match = False
        length_diff_pct = 0.9
        semantic_similarity = 1.0
        refusal_detected = False
        summary = "changed"

    monkeypatch.setattr(report, "compute_diff", lambda **kw: Diff())
    monkeypatch.setattr(report.time, "sleep", lambda *a: None)
    assert await report.main() == 0
    html = (blog / date.today().strftime("%Y-%m-%d-drift") / "index.html").read_text()
    assert html.count("</script>") == 3  # two external scripts and one inline
    assert "\\u003c/script>" in html
    assert "</script><img" not in html
