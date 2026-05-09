"""Pydantic models for API request/response validation."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.schemas import (
    Plan, SpecSchedule, RunStatus, DriftSeverity, Provider,
)


# ── Workspace ────────────────────────────────────────────────────────

class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    alert_email: Optional[str] = None
    slack_webhook_url: Optional[str] = None


class WorkspaceOut(BaseModel):
    id: UUID
    email: str
    name: str
    plan: Plan
    runs_this_month: int
    alert_email: Optional[str]
    slack_webhook_url: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── API Key ──────────────────────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    provider: Provider
    api_key: str = Field(..., min_length=1)
    label: str = "default"


class ApiKeyOut(BaseModel):
    id: UUID
    provider: Provider
    label: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Endpoint ─────────────────────────────────────────────────────────

class EndpointCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider: Provider
    model: str = Field(..., min_length=1, max_length=255)
    system_prompt: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 1024
    extra_params: dict = Field(default_factory=dict)


class EndpointUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    extra_params: Optional[dict] = None
    is_active: Optional[bool] = None


class EndpointOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    provider: Provider
    model: str
    system_prompt: Optional[str]
    temperature: float
    max_tokens: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Spec ─────────────────────────────────────────────────────────────

class SpecCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    input_text: str = Field(..., min_length=1)
    expected_format: Optional[str] = None
    expected_json_schema: Optional[dict] = None
    expected_contains: Optional[list[str]] = None
    expected_not_contains: Optional[list[str]] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    semantic_threshold: float = 0.85
    schedule: SpecSchedule = SpecSchedule.daily


class SpecUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    input_text: Optional[str] = None
    expected_format: Optional[str] = None
    expected_json_schema: Optional[dict] = None
    expected_contains: Optional[list[str]] = None
    expected_not_contains: Optional[list[str]] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    semantic_threshold: Optional[float] = None
    schedule: Optional[SpecSchedule] = None
    is_active: Optional[bool] = None


class SpecOut(BaseModel):
    id: UUID
    endpoint_id: UUID
    name: str
    description: Optional[str]
    input_text: str
    expected_format: Optional[str]
    expected_contains: Optional[list[str]]
    expected_not_contains: Optional[list[str]]
    min_length: Optional[int]
    max_length: Optional[int]
    semantic_threshold: float
    schedule: SpecSchedule
    is_active: bool
    has_baseline: bool = False
    baseline_set_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_baseline(cls, spec):
        return cls(
            **{c.name: getattr(spec, c.name) for c in spec.__table__.columns
               if c.name in cls.model_fields},
            has_baseline=spec.baseline_output is not None,
        )


# ── Run ──────────────────────────────────────────────────────────────

class RunOut(BaseModel):
    id: UUID
    spec_id: UUID
    status: RunStatus
    output_text: Optional[str]
    latency_ms: Optional[int]
    token_usage: Optional[dict]
    error_message: Optional[str]
    format_match: Optional[bool]
    length_diff_pct: Optional[float]
    semantic_similarity: Optional[float]
    refusal_detected: Optional[bool]
    drift_score: Optional[float]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class RunTrigger(BaseModel):
    """Manually trigger a run for a spec."""
    pass


# ── Drift Event ──────────────────────────────────────────────────────

class DriftEventOut(BaseModel):
    id: UUID
    spec_id: UUID
    run_id: UUID
    severity: DriftSeverity
    drift_score: float
    format_changed: bool
    length_drift_pct: Optional[float]
    semantic_drift: Optional[float]
    refusal_change: bool
    summary: Optional[str]
    email_sent: bool
    slack_sent: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Billing ──────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan: Plan
    success_url: str
    cancel_url: str


class CheckoutResponse(BaseModel):
    checkout_url: str


# ── Badge ────────────────────────────────────────────────────────────

class BadgeRequest(BaseModel):
    workspace_id: UUID


# ── Dashboard aggregates ────────────────────────────────────────────

class SpecHealth(BaseModel):
    spec_id: UUID
    spec_name: str
    endpoint_name: str
    status: str  # green / yellow / red
    last_drift_score: Optional[float]
    last_run_at: Optional[datetime]


class DriftTrend(BaseModel):
    date: datetime
    drift_score: float
    spec_id: UUID
