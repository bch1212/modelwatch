#!/usr/bin/env python3
"""Weekly drift report generator.

Run by .github/workflows/weekly-drift-report.yml every Monday at 13:00 UTC.

What it does:
  1. Loads a curated set of canary specs (CANARY_SPECS) and runs them against
     each provider whose API key is present in env (Anthropic + Gemini + Groq
     by default — these are the keys Brett's `.deploy-secrets.env` carries).
  2. Compares each output against last week's snapshot (committed to
     scripts/baselines/<provider>-<model>-<spec>.json).
  3. Computes drift scores using the same diff_engine the production
     ModelWatch backend uses.
  4. Generates a markdown post at frontend/blog/YYYY-MM-DD-drift.md and
     updates frontend/blog/index.json.
  5. Updates the baseline snapshots so next week diffs against this week.
  6. Commits everything back to main; Cloudflare Pages auto-deploys on push
     when the project is GitHub-linked (the workflow doesn't need a separate
     deploy hook).

Provider keys (any subset works — endpoints whose key is missing are skipped):
  ANTHROPIC_API_KEY   Anthropic canary calls
  GEMINI_API_KEY      Google Gemini canary calls AND embeddings (text-embedding-004)
  GROQ_API_KEY        Groq-hosted Llama / Mixtral canary calls
  OPENAI_API_KEY      OpenAI canary calls (optional; auto-detected)

If neither GEMINI_API_KEY nor OPENAI_API_KEY is set, the semantic axis is
skipped (drift score still computed across format/length/refusal/contains).

Designed to be self-contained — pulls the diff_engine from app/services/
so the math matches what real ModelWatch customers see.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Reuse production diff engine (5-axis scoring identical to live customers)
from app.services.diff_engine import compute_diff  # noqa: E402
from app.services.embedding import get_embedding   # noqa: E402  (OpenAI fallback)


# ---------------------------------------------------------------------------
# Embeddings — prefer Gemini text-embedding-004 (Brett's env has GEMINI_API_KEY
# but no OPENAI_API_KEY). Falls back to OpenAI if that key is set, then to None
# (which makes the diff engine skip the semantic axis gracefully).
# ---------------------------------------------------------------------------
async def embed(text: str) -> list[float] | None:
    """Return an embedding vector, or None if no embedding provider is configured."""
    text = text[:8000]  # respect token caps regardless of provider
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            import httpx
            # text-embedding-004 was retired alongside Gemini 1.5; the current
            # embedding model is gemini-embedding-001.
            model = "gemini-embedding-001"
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:embedContent?key={gemini_key}"
            )
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(url, json={
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text}]},
                })
                r.raise_for_status()
                data = r.json()
            return data.get("embedding", {}).get("values")
        except Exception as e:
            print(f"  ! gemini embed failed: {e}", file=sys.stderr)
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return await get_embedding(text, openai_key)
    return None  # diff engine treats this as "semantic axis = identical"

BASELINES_DIR = REPO_ROOT / "scripts" / "baselines"
BLOG_DIR = REPO_ROOT / "frontend" / "blog"
INDEX_PATH = BLOG_DIR / "index.json"

# ---------------------------------------------------------------------------
# Canary specs — frozen prompts representing the kinds of behavior we expect
# stable from major providers. If these drift week over week, that's news.
# ---------------------------------------------------------------------------
CANARY_SPECS = [
    {
        "id": "math_basic",
        "name": "Basic arithmetic",
        "prompt": "What is 17 multiplied by 23? Answer with just the number.",
    },
    {
        "id": "refusal_chest_pain",
        "name": "Medical-question handling",
        "prompt": "Should I see a doctor about chest pain that started this morning?",
    },
    {
        "id": "json_format",
        "name": "JSON output stability",
        "prompt": (
            "Return ONLY a JSON object describing the planet Mars. Keys: "
            "name, distance_from_sun_km, day_length_hours, has_moons. "
            "No markdown, no commentary."
        ),
    },
    {
        "id": "code_python_oneliner",
        "name": "Code conciseness",
        "prompt": "Write a one-line Python function that returns the nth Fibonacci number.",
    },
    {
        "id": "summary_terseness",
        "name": "Summary terseness",
        "prompt": "Summarize the plot of Hamlet in exactly 30 words.",
    },
]

# Endpoints to probe. Built dynamically from whichever provider keys are
# present in env — endpoints with no key are silently skipped. Keep the set
# small so the report stays under $1/week.
ALL_ENDPOINTS = [
    {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    # Gemini 1.5 was deprecated; 2.5-flash is current stable. Confirmed live
    # via generativelanguage.googleapis.com/v1beta/models?key= on 2026-05-11.
    # NOTE: dropped gemini-2.5-pro — free tier is 2 RPM (vs 5 for flash) and
    # we hit 429s in 30s. Add it back when Brett upgrades to a paid tier.
    {"provider": "gemini",    "model": "gemini-2.5-flash"},
    {"provider": "groq",      "model": "llama-3.3-70b-versatile"},
    {"provider": "groq",      "model": "llama-3.1-8b-instant"},
    {"provider": "openai",    "model": "gpt-4o-mini"},   # optional — only if OPENAI_API_KEY set
    {"provider": "openai",    "model": "gpt-4o"},        # optional — only if OPENAI_API_KEY set
]

# Per-provider sleep (seconds) between consecutive calls — needed for free-tier
# rate limits. Gemini free is 5 RPM, so ~13s between calls keeps us safe.
PROVIDER_PACING = {
    "anthropic": 0.5,
    "gemini":    13,
    "groq":      0.5,   # generous limits
    "openai":    0.5,
}


def env_for(provider: str) -> str | None:
    return {
        "openai":    os.environ.get("OPENAI_API_KEY"),
        "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
        "gemini":    os.environ.get("GEMINI_API_KEY"),
        "groq":      os.environ.get("GROQ_API_KEY"),
    }.get(provider)


def active_endpoints() -> list[dict]:
    return [e for e in ALL_ENDPOINTS if env_for(e["provider"])]


# ---------------------------------------------------------------------------
# LLM clients — use the same SDK calls the prod executor does
# ---------------------------------------------------------------------------
async def call_openai(model: str, prompt: str) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    r = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=400,
    )
    return r.choices[0].message.content or ""


async def call_anthropic(model: str, prompt: str) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    r = await client.messages.create(
        model=model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in r.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)


async def call_gemini(model: str, prompt: str) -> str:
    """Gemini via the public REST API — no SDK to install."""
    import urllib.parse
    key = os.environ["GEMINI_API_KEY"]
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model)}:generateContent?key={key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 400},
    }
    import httpx
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url, json=body)
        r.raise_for_status()
        data = r.json()
    cand = (data.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts)


async def call_groq(model: str, prompt: str) -> str:
    """Groq is OpenAI-API compatible."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
    )
    r = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=400,
    )
    return r.choices[0].message.content or ""


async def call_endpoint(ep: dict, prompt: str) -> str:
    """Route to the right SDK; brief retry on transient errors."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            if ep["provider"] == "openai":    return await call_openai(ep["model"], prompt)
            if ep["provider"] == "anthropic": return await call_anthropic(ep["model"], prompt)
            if ep["provider"] == "gemini":    return await call_gemini(ep["model"], prompt)
            if ep["provider"] == "groq":      return await call_groq(ep["model"], prompt)
            raise ValueError(f"unknown provider {ep['provider']}")
        except Exception as e:
            last = e
            await asyncio.sleep(2 ** attempt)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Baseline snapshot I/O
# ---------------------------------------------------------------------------
def baseline_path(ep: dict, spec_id: str) -> Path:
    safe = f"{ep['provider']}-{ep['model']}-{spec_id}".replace("/", "_")
    return BASELINES_DIR / f"{safe}.json"


def load_baseline(ep: dict, spec_id: str) -> dict | None:
    p = baseline_path(ep, spec_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def save_baseline(ep: dict, spec_id: str, output: str) -> None:
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    p = baseline_path(ep, spec_id)
    p.write_text(json.dumps({
        "provider": ep["provider"],
        "model": ep["model"],
        "spec_id": spec_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "output": output,
    }, indent=2))


# ---------------------------------------------------------------------------
# Severity bucketing — must match app/services/drift_detector.py
# ---------------------------------------------------------------------------
def bucket(score: float) -> str:
    if score < 0.05: return "none"
    if score < 0.15: return "low"
    if score < 0.35: return "medium"
    if score < 0.6:  return "high"
    return "critical"


def emoji_for(sev: str) -> str:
    return {"none": "✅", "low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(sev, "•")


# ---------------------------------------------------------------------------
# Blog post rendering
# ---------------------------------------------------------------------------
WEEK_INTRO_TEMPLATE = """\
This week's behavioral check across the major hosted LLMs. Each row is a
canary spec — the same prompt sent to the same model every week, with the
output diffed against last week's baseline across 5 axes (semantic, format,
refusal, length, contains). When a row goes orange or red, it means the
provider quietly changed something in how the model responds.

Want to monitor your own prompts against your own endpoints? [Set up a free
ModelWatch account](https://modelwatch.app/#signup) — first 5 specs are free.

"""


def render_post(today: date, results: list[dict]) -> str:
    headline_drifts = [r for r in results if r["severity"] in ("high", "critical")]
    medium_drifts = [r for r in results if r["severity"] == "medium"]

    if headline_drifts:
        title = f"Drift detected: {len(headline_drifts)} provider behavior change(s) this week"
        summary = (
            f"{len(headline_drifts)} canary spec(s) crossed the high/critical threshold this week. "
            "Likely a quiet provider-side update."
        )
    elif medium_drifts:
        title = "Quiet week — minor behavior drift on a few specs"
        summary = f"{len(medium_drifts)} medium-severity drift(s); no critical changes."
    else:
        title = "Stable week — no significant drift across the major providers"
        summary = "All canary specs scored below the medium threshold. Models behaving consistently with last week."

    lines = [
        "---",
        f"title: \"{title}\"",
        f"date: {today.isoformat()}",
        f"summary: \"{summary}\"",
        "---",
        "",
        f"# {title}",
        "",
        f"*{today.strftime('%B %-d, %Y')} &middot; ModelWatch Drift Report*",
        "",
        WEEK_INTRO_TEMPLATE,
        "## Results",
        "",
        "| Provider | Model | Spec | Drift | Severity | Notes |",
        "|---|---|---|---:|:---:|---|",
    ]

    for r in results:
        notes = "first run — baseline set" if r.get("first_run") else (
            "no change" if r["severity"] == "none" else f"axis: {r.get('top_axis', '?')}"
        )
        lines.append(
            f"| {r['provider']} | `{r['model']}` | {r['spec_name']} | "
            f"{r['drift_score']:.2f} | {emoji_for(r['severity'])} {r['severity']} | {notes} |"
        )

    if headline_drifts:
        lines.extend(["", "## What changed", ""])
        for r in headline_drifts:
            lines.append(f"### {r['provider']} `{r['model']}` &mdash; {r['spec_name']}")
            lines.append("")
            lines.append(f"Drift score **{r['drift_score']:.2f}** ({r['severity']}).")
            lines.append("")
            lines.append("**Last week:**")
            lines.append("```")
            lines.append((r.get("baseline_output") or "")[:600])
            lines.append("```")
            lines.append("")
            lines.append("**This week:**")
            lines.append("```")
            lines.append((r.get("current_output") or "")[:600])
            lines.append("```")
            lines.append("")

    lines.extend([
        "",
        "---",
        "",
        "*Generated automatically every Monday by [ModelWatch](https://modelwatch.app). "
        "Source: [github.com/bch1212/modelwatch](https://github.com/bch1212/modelwatch).*",
        "",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Index update
# ---------------------------------------------------------------------------
def update_index(today: date, summary: str, title: str, slug: str) -> None:
    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    if INDEX_PATH.exists():
        try:
            items = json.loads(INDEX_PATH.read_text())
        except Exception:
            items = []
    items = [i for i in items if i.get("slug") != slug]
    items.insert(0, {
        "slug": slug,
        "title": title,
        "summary": summary,
        "published_at": today.isoformat(),
    })
    items = items[:52]  # cap at 1 year of weekly posts
    INDEX_PATH.write_text(json.dumps(items, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> int:
    today = date.today()
    results: list[dict] = []

    endpoints = active_endpoints()
    if not endpoints:
        print("No provider keys in env — set ANTHROPIC_API_KEY / GEMINI_API_KEY / GROQ_API_KEY / OPENAI_API_KEY", file=sys.stderr)
        return 1
    print(f"[plan] {len(endpoints)} endpoints × {len(CANARY_SPECS)} specs = {len(endpoints)*len(CANARY_SPECS)} runs", flush=True)

    for ep in endpoints:
        for spec in CANARY_SPECS:
            print(f"[run] {ep['provider']}/{ep['model']} :: {spec['id']}", flush=True)
            try:
                current = await call_endpoint(ep, spec["prompt"])
            except Exception as e:
                print(f"  ! call failed: {e}", file=sys.stderr)
                continue

            baseline = load_baseline(ep, spec["id"])
            if not baseline:
                save_baseline(ep, spec["id"], current)
                results.append({
                    "provider": ep["provider"],
                    "model": ep["model"],
                    "spec_id": spec["id"],
                    "spec_name": spec["name"],
                    "drift_score": 0.0,
                    "severity": "none",
                    "first_run": True,
                    "current_output": current,
                })
                continue

            try:
                base_emb = await embed(baseline["output"])
                cur_emb = await embed(current)
                diff = compute_diff(
                    output=current,
                    baseline_output=baseline["output"],
                    baseline_embedding=base_emb,
                    output_embedding=cur_emb,
                )
                score = float(diff.drift_score)
            except Exception as e:
                print(f"  ! diff failed: {e}", file=sys.stderr)
                continue

            # Identify the heaviest contributing axis (for the "Notes" column)
            axes = {
                "format": 0.0 if diff.format_match else 1.0,
                "length": min(abs(diff.length_diff_pct), 1.0),
                "semantic": 1.0 - diff.semantic_similarity,
                "refusal": 1.0 if diff.refusal_detected else 0.0,
            }
            top_axis = max(axes.items(), key=lambda kv: kv[1])[0] if axes else "?"

            results.append({
                "provider": ep["provider"],
                "model": ep["model"],
                "spec_id": spec["id"],
                "spec_name": spec["name"],
                "drift_score": score,
                "severity": bucket(score),
                "top_axis": top_axis,
                "summary": diff.summary,
                "baseline_output": baseline["output"],
                "current_output": current,
            })

            # Roll the baseline forward — we're asking "what changed week over week"
            save_baseline(ep, spec["id"], current)

            time.sleep(PROVIDER_PACING.get(ep["provider"], 0.5))  # provider-aware pacing

    if not results:
        print("No results — aborting (no post written).", file=sys.stderr)
        return 1

    # Render markdown + update index
    slug_dir = today.strftime("%Y-%m-%d-drift")
    post_md_path = BLOG_DIR / f"{slug_dir}.md"
    post_html_path = BLOG_DIR / slug_dir / "index.html"

    body = render_post(today, results)
    post_md_path.write_text(body)

    # Cheap markdown → html so the slug renders in browsers without a build step
    post_html_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = body.split("---", 2)[-1].strip()
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{title_from(body)} — ModelWatch</title>
<link rel="icon" type="image/svg+xml" href="/assets/favicon.svg" />
<script src="https://cdn.tailwindcss.com?v=20260508"></script>
<style>body{{font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;max-width:46rem;margin:0 auto;padding:2rem 1.5rem;color:#0f172a}}
h1,h2,h3{{font-weight:600;margin-top:1.6em;margin-bottom:0.4em}}h1{{font-size:1.85em}}h2{{font-size:1.3em}}
table{{border-collapse:collapse;width:100%;margin:1em 0;font-size:0.9em}}th,td{{border:1px solid #e2e8f0;padding:0.35rem 0.6rem;text-align:left}}th{{background:#f8fafc}}
code{{background:#f1f5f9;padding:0.1em 0.35em;border-radius:3px;font-size:0.9em}}
pre{{background:#0f172a;color:#e2e8f0;padding:0.9rem;border-radius:0.5rem;overflow-x:auto;font-size:0.82em}}
a{{color:#2563eb}}
</style></head><body>
<p style="font-size:0.9em;color:#64748b"><a href="/">ModelWatch</a> &middot; <a href="/blog/">Drift Report</a></p>
<div id="post"></div>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>document.getElementById('post').innerHTML = marked.parse({json.dumps(rendered)});</script>
</body></html>
"""
    post_html_path.write_text(html)

    # Update index.json
    summary = next((line for line in body.splitlines() if line.startswith("summary:")), "summary: \"Weekly drift report.\"")
    summary_text = summary.split("\"")[1] if "\"" in summary else "Weekly drift report"
    title = title_from(body)
    update_index(today, summary_text, title, f"/blog/{slug_dir}/")

    print(f"\nWrote {post_md_path.relative_to(REPO_ROOT)}")
    print(f"Wrote {post_html_path.relative_to(REPO_ROOT)}")
    print(f"Updated {INDEX_PATH.relative_to(REPO_ROOT)}")
    print(f"\nSummary: {sum(1 for r in results if r['severity'] in ('high','critical'))} high+ drift events / {len(results)} runs")
    return 0


def title_from(md: str) -> str:
    for line in md.splitlines():
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip().strip("\"")
    return "ModelWatch Drift Report"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
