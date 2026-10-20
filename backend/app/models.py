import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        CheckConstraint("language IN ('en', 'de')", name="ck_articles_language"),
        CheckConstraint(
            "import_type IN ('manual', 'url')", name="ck_articles_import_type"
        ),
        Index("ix_articles_content_hash", "content_hash", unique=True),
        Index("ix_articles_created_at", "created_at"),
        Index("ix_articles_title", "title"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(2))
    import_type: Mapped[str] = mapped_column(String(10))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    scripts: Mapped[list["ScriptVersion"]] = relationship(back_populates="article")


class ImportAttempt(Base):
    __tablename__ = "import_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'duplicate', 'failed')",
            name="ck_import_attempts_status",
        ),
        Index("ix_import_attempts_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("articles.id"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    article: Mapped[Article | None] = relationship()


class ScriptVersion(Base):
    __tablename__ = "script_versions"
    __table_args__ = (
        CheckConstraint("source IN ('generated', 'edited')", name="ck_scripts_source"),
        CheckConstraint("target_language IN ('en', 'de')", name="ck_scripts_language"),
        Index("ux_scripts_article_version", "article_id", "version", unique=True),
        Index("ix_scripts_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("articles.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="generated")
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("script_versions.id"), nullable=True
    )
    target_language: Mapped[str] = mapped_column(String(2), nullable=False)
    generation_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(200), nullable=False)
    actual_model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    article: Mapped[Article] = relationship(back_populates="scripts")
    parent_version: Mapped["ScriptVersion | None"] = relationship(remote_side=[id])


class ProductionJob(Base):
    __tablename__ = "production_jobs"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('script_generation', 'script_evaluation')",
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
        DateTime(timezone=True), default=utc_now, server_default=func.now(), onupdate=utc_now
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
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class ProviderCall(Base):
    __tablename__ = "provider_calls"
    __table_args__ = (Index("ix_provider_calls_job", "job_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
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
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'needs_attention')",
            name="ck_evaluations_status",
        ),
        CheckConstraint(
            "factual_consistency IS NULL OR (factual_consistency >= 0 AND factual_consistency <= 1)",
            name="ck_evaluations_factual_score",
        ),
        CheckConstraint(
            "content_coverage IS NULL OR (content_coverage >= 0 AND content_coverage <= 1)",
            name="ck_evaluations_coverage_score",
        ),
        CheckConstraint(
            "audio_readiness IS NULL OR (audio_readiness >= 0 AND audio_readiness <= 1)",
            name="ck_evaluations_audio_score",
        ),
        Index("ux_evaluations_job", "job_id", unique=True),
        Index("ix_evaluations_script_created", "script_version_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    script_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_versions.id"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("production_jobs.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    factual_consistency: Mapped[float | None] = mapped_column(Numeric(4, 3))
    content_coverage: Mapped[float | None] = mapped_column(Numeric(4, 3))
    audio_readiness: Mapped[float | None] = mapped_column(Numeric(4, 3))
    issues: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32))
    requested_model: Mapped[str | None] = mapped_column(String(200))
    actual_model: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    script_version: Mapped[ScriptVersion] = relationship()
    job: Mapped[ProductionJob] = relationship(foreign_keys=[job_id])


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ux_approvals_evaluation", "evaluation_id", unique=True),
        Index("ix_approvals_script_created", "script_version_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    script_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_versions.id"), nullable=False
    )
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evaluations.id"), nullable=False
    )
    acknowledged_critical_issue_ids: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    script_version: Mapped[ScriptVersion] = relationship()
    evaluation: Mapped[Evaluation] = relationship()
