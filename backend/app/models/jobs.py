import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now
from app.models.articles import Article
from app.models.base import Base
from app.models.scripts import ScriptVersion


class ProductionJob(Base):
    __tablename__ = "production_jobs"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('script_generation', 'script_evaluation', 'audio_generation')",
            name="ck_jobs_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_scheduled', 'succeeded', 'failed', 'needs_attention')",
            name="ck_jobs_status",
        ),
        Index("ux_jobs_idempotency_key", "idempotency_key", unique=True),
        Index("ix_jobs_article_created", "article_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    retry_of_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("production_jobs.id"), unique=True
    )
    root_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("production_jobs.id")
    )
    current_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("job_executions.id", use_alter=True, name="fk_jobs_execution")
    )
    provider_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("articles.id"), nullable=False
    )
    source_script_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("script_versions.id"), nullable=True
    )
    result_script_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("script_versions.id"), nullable=True
    )
    result_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("evaluations.id"), nullable=True
    )
    result_audio_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("audio_assets.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    article: Mapped[Article] = relationship(foreign_keys=[article_id])
    source_script_version: Mapped[ScriptVersion | None] = relationship(
        foreign_keys=[source_script_version_id]
    )
    result_script_version: Mapped[ScriptVersion | None] = relationship(
        foreign_keys=[result_script_version_id]
    )
    result_evaluation: Mapped["Evaluation | None"] = relationship(
        foreign_keys=[result_evaluation_id]
    )


class TaskOutbox(Base):
    __tablename__ = "task_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'dispatched')", name="ck_outbox_status"),
        Index("ux_outbox_job", "job_id", unique=True),
        Index("ix_outbox_due", "status", "available_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("production_jobs.id"), nullable=False
    )
    task_name: Mapped[str] = mapped_column(String(100), nullable=False)
    queue_name: Mapped[str] = mapped_column(
        String(64), default="script_generation", nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    job: Mapped[ProductionJob] = relationship()


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_created", "job_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("production_jobs.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    event_metadata: Mapped[dict] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class ProviderCall(Base):
    __tablename__ = "provider_calls"
    __table_args__ = (Index("ix_provider_calls_job", "job_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("job_executions.id")
    )
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("production_jobs.id"), nullable=False
    )
    operation: Mapped[str] = mapped_column(
        String(32), default="script_generation", nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(200), nullable=False)
    actual_model: Mapped[str | None] = mapped_column(String(200))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[float | None] = mapped_column(Numeric(14, 8))
    input_characters: Mapped[int | None] = mapped_column(Integer)
    provider_credits: Mapped[float | None] = mapped_column(Numeric(14, 4))
    audio_duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 3))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobExecution(Base):
    __tablename__ = "job_executions"
    __table_args__ = (
        Index("ux_execution_attempt", "job_id", "attempt_no", unique=True),
        Index("ix_execution_lease", "status", "lease_expires_at"),
        CheckConstraint(
            "status IN ('running','succeeded','failed','abandoned')",
            name="ck_execution_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("production_jobs.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(32), default="preparing")
    status: Mapped[str] = mapped_column(String(16), default="running")
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobRetryRequest(Base):
    __tablename__ = "job_retry_requests"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("production_jobs.id"))


class MaintenanceHeartbeat(Base):
    __tablename__ = "maintenance_heartbeats"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


if TYPE_CHECKING:
    from app.models.reviews import Evaluation
