import uuid
from datetime import datetime

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base


class AudioAsset(Base):
    __tablename__ = "audio_assets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('available', 'deleting', 'delete_failed', 'deleted')",
            name="ck_audio_status",
        ),
        CheckConstraint("language IN ('en', 'de')", name="ck_audio_language"),
        CheckConstraint("speed >= 0.7 AND speed <= 1.2", name="ck_audio_speed"),
        Index("ux_audio_job", "job_id", unique=True),
        Index("ix_audio_created", "created_at"),
        Index("ix_audio_script", "script_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("articles.id"))
    script_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_versions.id")
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("approvals.id"))
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("production_jobs.id"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(2))
    voice_id: Mapped[str] = mapped_column(String(200))
    voice_name: Mapped[str] = mapped_column(String(200))
    speed: Mapped[float] = mapped_column(Numeric(4, 2))
    format: Mapped[str] = mapped_column(String(32), default="mp3_44100_128")
    mime_type: Mapped[str] = mapped_column(String(64), default="audio/mpeg")
    bucket: Mapped[str] = mapped_column(String(100))
    object_key: Mapped[str] = mapped_column(String(200))
    duration_seconds: Mapped[float] = mapped_column(Numeric(12, 3))
    file_size: Mapped[int] = mapped_column(Integer)
    input_characters: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="available")
    delete_error: Mapped[str | None] = mapped_column(Text)
    delete_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class AudioRequest(Base):
    """Retain keys even when multiple requests reuse the same audio job."""

    __tablename__ = "audio_requests"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("production_jobs.id"))


class AudioArtifact(Base):
    __tablename__ = "audio_artifacts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("production_jobs.id"), index=True
    )
    provider_call_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("provider_calls.id"), unique=True
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("approvals.id"))
    audio_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    staging_path: Mapped[str] = mapped_column(String(300))
    object_key: Mapped[str] = mapped_column(String(200), unique=True)
    bucket: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="preparing")
    sha256: Mapped[str | None] = mapped_column(String(64))
    file_size: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
