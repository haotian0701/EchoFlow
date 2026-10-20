import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now
from app.models.base import Base


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


if TYPE_CHECKING:
    from app.models.scripts import ScriptVersion
