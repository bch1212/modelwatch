"""Drift detector — orchestrates running a spec, computing diffs, and creating events.

This is the core loop:
  1. Fetch endpoint + decrypted API key
  2. Execute LLM call
  3. Get embedding (if OpenAI key available)
  4. If first run → set as baseline
  5. Else → compute diff against baseline
  6. If drift exceeds threshold → create DriftEvent
  7. Return the Run record
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import (
    Spec, Endpoint, ApiKey, Run, DriftEvent,
    RunStatus, DriftSeverity, Provider,
)
from app.services import encryption, llm_executor, embedding
from app.services.diff_engine import compute_diff


def _severity_from_score(score: float) -> DriftSeverity:
    if score < 0.05:
        return DriftSeverity.none
    if score < 0.15:
        return DriftSeverity.low
    if score < 0.35:
        return DriftSeverity.medium
    if score < 0.6:
        return DriftSeverity.high
    return DriftSeverity.critical


async def _get_api_key(
    db: AsyncSession, workspace_id: UUID, provider: Provider
) -> Optional[str]:
    """Fetch and decrypt the customer's API key for a provider."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.workspace_id == workspace_id,
            ApiKey.provider == provider,
        ).limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    return encryption.decrypt(row.encrypted_key)


async def _get_openai_key(db: AsyncSession, workspace_id: UUID) -> Optional[str]:
    """Get OpenAI key for embeddings — may differ from the endpoint provider."""
    return await _get_api_key(db, workspace_id, Provider.openai)


async def run_spec(db: AsyncSession, spec: Spec) -> Run:
    """Execute a single spec: call LLM, diff, detect drift, persist."""

    endpoint: Endpoint = await db.get(Endpoint, spec.endpoint_id)
    if not endpoint:
        run = Run(
            spec_id=spec.id,
            status=RunStatus.failed,
            error_message="Endpoint not found",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        db.add(run)
        return run

    # Get decrypted API key
    api_key = await _get_api_key(db, endpoint.workspace_id, endpoint.provider)
    if not api_key:
        run = Run(
            spec_id=spec.id,
            status=RunStatus.failed,
            error_message=f"No {endpoint.provider.value} API key configured",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        db.add(run)
        return run

    # Execute LLM call
    started = datetime.now(timezone.utc)
    llm_resp = await llm_executor.execute(
        provider=endpoint.provider,
        api_key=api_key,
        model=endpoint.model,
        input_text=spec.input_text,
        system_prompt=endpoint.system_prompt,
        temperature=endpoint.temperature,
        max_tokens=endpoint.max_tokens,
        extra_params=endpoint.extra_params,
    )
    completed = datetime.now(timezone.utc)

    if llm_resp.error:
        run = Run(
            spec_id=spec.id,
            status=RunStatus.failed,
            error_message=llm_resp.error,
            latency_ms=llm_resp.latency_ms,
            token_usage=llm_resp.token_usage,
            started_at=started,
            completed_at=completed,
        )
        db.add(run)
        return run

    # Get embedding (for semantic comparison)
    openai_key = await _get_openai_key(db, endpoint.workspace_id)
    output_emb = None
    if openai_key:
        output_emb = await embedding.get_embedding(llm_resp.output_text, openai_key)

    # First run → set baseline
    if spec.baseline_output is None:
        run = Run(
            spec_id=spec.id,
            status=RunStatus.completed,
            output_text=llm_resp.output_text,
            output_embedding=output_emb,
            latency_ms=llm_resp.latency_ms,
            token_usage=llm_resp.token_usage,
            format_match=True,
            length_diff_pct=0.0,
            semantic_similarity=1.0,
            refusal_detected=False,
            drift_score=0.0,
            started_at=started,
            completed_at=completed,
        )
        db.add(run)
        await db.flush()  # get run.id

        spec.baseline_output = llm_resp.output_text
        spec.baseline_embedding = output_emb
        spec.baseline_run_id = run.id
        spec.baseline_set_at = datetime.now(timezone.utc)
        return run

    # Compute diff against baseline
    diff = compute_diff(
        output=llm_resp.output_text,
        baseline_output=spec.baseline_output,
        baseline_embedding=spec.baseline_embedding,
        output_embedding=output_emb,
        expected_format=spec.expected_format,
        expected_json_schema=spec.expected_json_schema,
        expected_contains=spec.expected_contains,
        expected_not_contains=spec.expected_not_contains,
        min_length=spec.min_length,
        max_length=spec.max_length,
        semantic_threshold=spec.semantic_threshold,
    )

    run = Run(
        spec_id=spec.id,
        status=RunStatus.completed,
        output_text=llm_resp.output_text,
        output_embedding=output_emb,
        latency_ms=llm_resp.latency_ms,
        token_usage=llm_resp.token_usage,
        format_match=diff.format_match,
        length_diff_pct=diff.length_diff_pct,
        semantic_similarity=diff.semantic_similarity,
        refusal_detected=diff.refusal_detected,
        drift_score=diff.drift_score,
        started_at=started,
        completed_at=completed,
    )
    db.add(run)
    await db.flush()

    # Create drift event if threshold exceeded
    severity = _severity_from_score(diff.drift_score)
    if severity != DriftSeverity.none:
        event = DriftEvent(
            spec_id=spec.id,
            run_id=run.id,
            severity=severity,
            drift_score=diff.drift_score,
            format_changed=not diff.format_match,
            length_drift_pct=diff.length_diff_pct,
            semantic_drift=1.0 - diff.semantic_similarity,
            refusal_change=diff.refusal_detected,
            summary=diff.summary,
        )
        db.add(event)
        await db.flush()

    return run
