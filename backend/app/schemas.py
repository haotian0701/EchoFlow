from datetime import datetime
from typing import Any, Literal
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


TargetLanguage = Literal["en", "de"]
ScriptLength = Literal["short", "medium", "long"]
ScriptStyle = Literal["news", "conversational", "professional"]
ScriptAudience = Literal["general", "business"]
JobStatus = Literal[
    "queued", "running", "retry_scheduled", "succeeded", "failed", "needs_attention"
]


class ScriptGenerationConfig(BaseModel):
    target_language: TargetLanguage
    length: ScriptLength
    style: ScriptStyle
    audience: ScriptAudience


class JobCreate(BaseModel):
    type: Literal["script_generation"]
    article_id: UUID
    config: ScriptGenerationConfig


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: Literal["script_generation"]
    article_id: UUID
    source_script_version_id: UUID | None
    result_script_version_id: UUID | None
    status: JobStatus
    stage: str
    config: ScriptGenerationConfig
    attempt_count: int
    max_attempts: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class JobCreateResult(BaseModel):
    job: JobOut
    idempotent_replay: bool


class JobList(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class JobEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    event_type: str
    stage: str
    message: str
    metadata: dict[str, Any] = Field(validation_alias="event_metadata")
    created_at: datetime


class JobEventList(BaseModel):
    items: list[JobEventOut]


class ScriptSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    article_id: UUID
    version: int
    source: Literal["generated", "edited"]
    parent_version_id: UUID | None
    target_language: TargetLanguage
    generation_config: ScriptGenerationConfig
    provider: str
    requested_model: str
    actual_model: str
    prompt_version: str
    estimated_duration_seconds: int
    created_at: datetime


class ScriptDetail(ScriptSummary):
    text: str


class ScriptList(BaseModel):
    items: list[ScriptSummary]
    total: int
    limit: int
    offset: int


class RegenerateRequest(BaseModel):
    config: ScriptGenerationConfig | None = None
