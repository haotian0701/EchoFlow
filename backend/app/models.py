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
        CheckConstraint("job_type = 'script_generation'", name="ck_jobs_type"),
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
