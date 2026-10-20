import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now
from app.models.articles import Article
from app.models.base import Base


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
