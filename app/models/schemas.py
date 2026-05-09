"""SQLAlchemy ORM models for ModelWatch."""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, Text, DateTime, Integer, Float, Boolean,
    ForeignKey, JSON, Enum, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.database import Base


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return uuid.uuid4()


# ── Enums ────────────────────────────────────────────────────────────

class Plan(str, PyEnum):
    free = "free"
    pro = "pro"
    team = "team"
    enterprise = "enterprise"


class SpecSchedule(str, PyEnum):
    hourly = "hourly"
    daily = "daily"
    weekly = "weekly"
    on_trigger = "on_trigger"


class RunStatus(str, PyEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class DriftSeverity(str, PyEnum):
    none = "none"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Provider(str, PyEnum):
    openai = "openai"
    anthropic = "anthropic"


# ── Models ───────────────────────────────────────────────────────────

class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_id)
    email = Column(String(255), unique=True, nullable=False, index=True)
    api_key = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    plan = Column(Enum(Plan), default=Plan.free, nullable=False)
    stripe_customer_id = Column(String(255), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    runs_this_month = Column(Integer, default=0, nullable=False)
    runs_reset_at = Column(DateTime(timezone=True), default=utcnow)
    alert_email = Column(String(255), nullable=True)
    slack_webhook_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    endpoints = relationship("Endpoint", back_populates="workspace", cascade="all, delete-orphan")
    api_keys = relationship("ApiKey", back_populates="workspace", cascade="all, delete-orphan")


class ApiKey(Base):
    """Encrypted customer-provided LLM API keys."""
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_id)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    provider = Column(Enum(Provider), nullable=False)
    encrypted_key = Column(Text, nullable=False)  # Fernet-encrypted
    label = Column(String(255), default="default")
    created_at = Column(DateTime(timezone=True), default=utcnow)

    workspace = relationship("Workspace", back_populates="api_keys")

    __table_args__ = (
        Index("ix_apikey_workspace_provider", "workspace_id", "provider"),
    )


class Endpoint(Base):
    """An LLM endpoint the user wants to monitor."""
    __tablename__ = "endpoints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_id)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    provider = Column(Enum(Provider), nullable=False)
    model = Column(String(255), nullable=False)  # e.g. "gpt-4o", "claude-sonnet-4-20250514"
    system_prompt = Column(Text, nullable=True)
    temperature = Column(Float, default=0.0)
    max_tokens = Column(Integer, default=1024)
    extra_params = Column(JSON, default=dict)  # any additional model params
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    workspace = relationship("Workspace", back_populates="endpoints")
    specs = relationship("Spec", back_populates="endpoint", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_endpoint_workspace", "workspace_id"),
    )


class Spec(Base):
    """A behavioral test case: input → expected output rules."""
    __tablename__ = "specs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_id)
    endpoint_id = Column(UUID(as_uuid=True), ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # The test input
    input_text = Column(Text, nullable=False)

    # Expected behavior rules
    expected_format = Column(String(50), nullable=True)  # "json", "markdown", "plain"
    expected_json_schema = Column(JSON, nullable=True)  # JSON schema the output must match
    expected_contains = Column(JSON, nullable=True)  # list of strings that must appear
    expected_not_contains = Column(JSON, nullable=True)  # list of strings that must NOT appear
    min_length = Column(Integer, nullable=True)
    max_length = Column(Integer, nullable=True)
    semantic_threshold = Column(Float, default=0.85)  # cosine similarity vs baseline

    # Scheduling
    schedule = Column(Enum(SpecSchedule), default=SpecSchedule.daily)
    is_active = Column(Boolean, default=True)

    # Baseline (first successful run output)
    baseline_output = Column(Text, nullable=True)
    baseline_embedding = Column(JSON, nullable=True)  # stored as list[float]
    baseline_run_id = Column(UUID(as_uuid=True), nullable=True)
    baseline_set_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    endpoint = relationship("Endpoint", back_populates="specs")
    runs = relationship("Run", back_populates="spec", cascade="all, delete-orphan")
    drift_events = relationship("DriftEvent", back_populates="spec", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_spec_endpoint", "endpoint_id"),
    )


class Run(Base):
    """A single execution of a spec against its endpoint."""
    __tablename__ = "runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_id)
    spec_id = Column(UUID(as_uuid=True), ForeignKey("specs.id", ondelete="CASCADE"), nullable=False)
    status = Column(Enum(RunStatus), default=RunStatus.pending, nullable=False)

    # LLM response
    output_text = Column(Text, nullable=True)
    output_embedding = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    token_usage = Column(JSON, nullable=True)  # {"prompt_tokens": ..., "completion_tokens": ...}
    error_message = Column(Text, nullable=True)

    # Diff scores (computed against baseline)
    format_match = Column(Boolean, nullable=True)
    length_diff_pct = Column(Float, nullable=True)  # % change vs baseline
    semantic_similarity = Column(Float, nullable=True)  # cosine sim vs baseline
    refusal_detected = Column(Boolean, nullable=True)
    drift_score = Column(Float, nullable=True)  # composite 0–1 (higher = more drift)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    spec = relationship("Spec", back_populates="runs")

    __table_args__ = (
        Index("ix_run_spec_created", "spec_id", "created_at"),
    )


class DriftEvent(Base):
    """Created when a run exceeds drift thresholds."""
    __tablename__ = "drift_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_id)
    spec_id = Column(UUID(as_uuid=True), ForeignKey("specs.id", ondelete="CASCADE"), nullable=False)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    severity = Column(Enum(DriftSeverity), nullable=False)

    # What changed
    drift_score = Column(Float, nullable=False)
    format_changed = Column(Boolean, default=False)
    length_drift_pct = Column(Float, nullable=True)
    semantic_drift = Column(Float, nullable=True)  # 1 - similarity
    refusal_change = Column(Boolean, default=False)
    summary = Column(Text, nullable=True)  # human-readable diff summary

    # Alert status
    email_sent = Column(Boolean, default=False)
    slack_sent = Column(Boolean, default=False)
    webhook_sent = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), default=utcnow)

    spec = relationship("Spec", back_populates="drift_events")
    run = relationship("Run")

    __table_args__ = (
        Index("ix_drift_spec_created", "spec_id", "created_at"),
    )
