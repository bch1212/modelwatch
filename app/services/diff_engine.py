"""Diff computation engine — compares a run's output against the baseline.

Computes:
  1. Format diff     — did the output format change? (JSON/markdown/plain)
  2. Length diff      — significant length change vs baseline
  3. Semantic diff    — cosine similarity via embedding
  4. Refusal detect   — did the model refuse a previously-successful request?
  5. Contains checks  — required/forbidden strings
  6. Composite score  — weighted 0-1 drift score
"""

import json
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np


# ── Refusal patterns ─────────────────────────────────────────────────

REFUSAL_PATTERNS = [
    r"i (?:can't|cannot|won't|will not|am not able to|'m not able to)",
    r"i'm sorry.{0,30}(?:can't|cannot|unable|not able)",
    r"as an ai",
    r"i(?:'m| am) not (?:able|allowed|permitted)",
    r"my (?:guidelines|policies|programming) (?:prevent|don't allow|do not allow)",
    r"this (?:request|content) (?:violates|goes against)",
    r"i must (?:decline|refuse)",
]
_REFUSAL_RE = re.compile("|".join(REFUSAL_PATTERNS), re.IGNORECASE)


@dataclass
class DiffResult:
    format_match: bool
    length_diff_pct: float          # % change, e.g. 0.25 = 25% longer
    semantic_similarity: float      # 0-1, 1 = identical
    refusal_detected: bool
    contains_pass: bool             # all required strings present
    not_contains_pass: bool         # no forbidden strings present
    drift_score: float              # composite 0-1
    summary: str                    # human-readable


# ── Helpers ──────────────────────────────────────────────────────────

def detect_format(text: str) -> str:
    """Guess output format: json, markdown, or plain."""
    stripped = text.strip()
    # JSON?
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass
    # Markdown?
    md_signals = ["```", "## ", "### ", "- [ ]", "| ", "**"]
    if any(sig in text for sig in md_signals):
        return "markdown"
    return "plain"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    dot = float(np.dot(va, vb))
    norm = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if norm == 0:
        return 0.0
    return dot / norm


def is_refusal(text: str) -> bool:
    """Detect if the model output is a refusal."""
    return bool(_REFUSAL_RE.search(text[:500]))


def check_json_schema(text: str, schema: dict) -> bool:
    """Very basic JSON schema check — validates required keys exist."""
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    required = schema.get("required", [])
    if isinstance(data, dict):
        return all(k in data for k in required)
    return True


# ── Main diff function ───────────────────────────────────────────────

def compute_diff(
    output: str,
    baseline_output: str,
    baseline_embedding: Optional[list[float]],
    output_embedding: Optional[list[float]],
    expected_format: Optional[str] = None,
    expected_json_schema: Optional[dict] = None,
    expected_contains: Optional[list[str]] = None,
    expected_not_contains: Optional[list[str]] = None,
    min_length: Optional[int] = None,
    max_length: Optional[int] = None,
    semantic_threshold: float = 0.85,
) -> DiffResult:
    """Compare a run output against the baseline + spec rules."""

    issues: list[str] = []

    # 1. Format check
    actual_format = detect_format(output)
    if expected_format:
        fmt_match = actual_format == expected_format
    else:
        fmt_match = actual_format == detect_format(baseline_output)
    if not fmt_match:
        issues.append(f"Format changed: expected {expected_format or detect_format(baseline_output)}, got {actual_format}")

    # JSON schema
    if expected_json_schema and not check_json_schema(output, expected_json_schema):
        issues.append("Output does not match expected JSON schema")

    # 2. Length diff
    baseline_len = len(baseline_output)
    output_len = len(output)
    if baseline_len > 0:
        length_diff_pct = (output_len - baseline_len) / baseline_len
    else:
        length_diff_pct = 1.0 if output_len > 0 else 0.0
    if abs(length_diff_pct) > 0.3:
        direction = "longer" if length_diff_pct > 0 else "shorter"
        issues.append(f"Length {direction} by {abs(length_diff_pct)*100:.0f}%")

    # Length bounds
    if min_length and output_len < min_length:
        issues.append(f"Output too short ({output_len} < {min_length})")
    if max_length and output_len > max_length:
        issues.append(f"Output too long ({output_len} > {max_length})")

    # 3. Semantic similarity
    if output_embedding and baseline_embedding:
        sem_sim = cosine_similarity(output_embedding, baseline_embedding)
    else:
        sem_sim = 1.0  # can't compute, assume identical
    if sem_sim < semantic_threshold:
        issues.append(f"Semantic similarity dropped to {sem_sim:.3f} (threshold {semantic_threshold})")

    # 4. Refusal detection
    baseline_refused = is_refusal(baseline_output)
    output_refused = is_refusal(output)
    refusal_detected = output_refused and not baseline_refused
    if refusal_detected:
        issues.append("Model now refuses a request it previously completed")

    # 5. Contains checks
    contains_pass = True
    if expected_contains:
        missing = [s for s in expected_contains if s.lower() not in output.lower()]
        if missing:
            contains_pass = False
            issues.append(f"Missing required strings: {missing}")

    not_contains_pass = True
    if expected_not_contains:
        found = [s for s in expected_not_contains if s.lower() in output.lower()]
        if found:
            not_contains_pass = False
            issues.append(f"Forbidden strings found: {found}")

    # 6. Composite drift score (0 = no drift, 1 = max drift)
    scores = []
    # Format: 0 or 1
    scores.append(0.0 if fmt_match else 1.0)
    # Length: clamped 0-1
    scores.append(min(abs(length_diff_pct), 1.0))
    # Semantic: invert similarity
    scores.append(1.0 - sem_sim)
    # Refusal: 0 or 1
    scores.append(1.0 if refusal_detected else 0.0)
    # Contains: 0 or 0.5
    scores.append(0.0 if contains_pass and not_contains_pass else 0.5)

    # Weighted average: semantic heaviest, then format, refusal, length, contains
    weights = [0.2, 0.15, 0.35, 0.2, 0.1]
    drift_score = sum(s * w for s, w in zip(scores, weights))

    summary = "; ".join(issues) if issues else "No significant drift detected"

    return DiffResult(
        format_match=fmt_match,
        length_diff_pct=length_diff_pct,
        semantic_similarity=sem_sim,
        refusal_detected=refusal_detected,
        contains_pass=contains_pass,
        not_contains_pass=not_contains_pass,
        drift_score=drift_score,
        summary=summary,
    )
