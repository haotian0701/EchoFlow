from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Language = Literal["en", "de"]


def trimmed(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class ArticleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=100, max_length=30000)
    source: str = Field(min_length=1, max_length=200)
    language: Language

    _trim_title = field_validator("title", "source", mode="before")(trimmed)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not 100 <= len(value.strip()) <= 30000:
            raise ValueError("Article text must contain 100–30,000 characters")
        return value


class ArticleImport(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    language: Language
    title: str | None = Field(default=None, max_length=300)
    source: str | None = Field(default=None, max_length=200)

    _trim_fields = field_validator("url", "title", "source", mode="before")(
        lambda value: value.strip() if isinstance(value, str) else value
    )


class ArticleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    source: str
    source_url: str | None
    language: Language
    import_type: Literal["manual", "url"]
    created_at: datetime


class ArticleDetail(ArticleSummary):
    original_text: str
    content_hash: str


class ImportAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    status: Literal["succeeded", "duplicate", "failed"]
    article_id: UUID | None
    error_code: str | None
    error_message: str | None
    created_at: datetime


class ArticleWriteResult(BaseModel):
    article: ArticleDetail
    duplicate: bool
    import_attempt: ImportAttemptOut | None = None


class ArticleList(BaseModel):
    items: list[ArticleSummary]
    total: int
    limit: int
    offset: int


class ImportAttemptList(BaseModel):
    items: list[ImportAttemptOut]
    total: int
    limit: int
    offset: int
