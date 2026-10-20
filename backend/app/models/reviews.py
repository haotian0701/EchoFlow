import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now
from app.models.base import Base
from app.models.jobs import ProductionJob
from app.models.scripts import ScriptVersion


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
