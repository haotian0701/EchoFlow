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
    job_type: Literal["script_generation", "script_evaluation", "audio_generation"]
    article_id: UUID
    source_script_version_id: UUID | None
    result_script_version_id: UUID | None
    result_evaluation_id: UUID | None
    result_audio_asset_id: UUID | None
    status: JobStatus
    stage: str
    config: dict[str, Any]
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


class AudioCreate(BaseModel):
    language: Language
    voice_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    speed: float = Field(default=1.0, ge=0.7, le=1.2, multiple_of=0.01, allow_inf_nan=False)


class TTSVoice(BaseModel):
    id: str
    name: str
    provider: str
    supported_languages: list[Language]
    verified_languages: list[str]
    labels: dict[str, str]
    preview_url: str | None


class TTSVoiceList(BaseModel):
    items: list[TTSVoice]
    cached: bool


class AudioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    article_id: UUID
    script_version_id: UUID
    approval_id: UUID
    job_id: UUID
    provider: str
    model: str
    language: Language
    voice_id: str
    voice_name: str
    speed: float
    format: str
    mime_type: str
    duration_seconds: float
    file_size: int
    input_characters: int
    status: Literal["available", "deleting", "delete_failed", "deleted"]
    delete_error: str | None
    deleted_at: datetime | None
    created_at: datetime
    article_title: str = ""
    script_version: int = 0


class AudioList(BaseModel):
    items: list[AudioOut]
    total: int
    limit: int
    offset: int


class AudioCreateResult(BaseModel):
    job: JobOut
    audio: AudioOut | None = None
    idempotent_replay: bool
    reused_existing: bool


class AudioProviderCall(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    provider: str
    requested_model: str
    actual_model: str | None
    provider_request_id: str | None
    input_characters: int | None
    provider_credits: float | None
    audio_duration_seconds: float | None
    latency_ms: int | None
    error_code: str | None


class AudioDetail(AudioOut):
    article: ArticleSummary
    job: JobOut
    provider_calls: list[AudioProviderCall]


EvaluationStatus = Literal[
    "queued", "running", "succeeded", "failed", "needs_attention"
]
EvaluationDimension = Literal[
    "factual_consistency", "content_coverage", "audio_readiness"
]
IssueSeverity = Literal["info", "warning", "critical"]
EvidenceStatus = Literal["verified", "insufficient", "not_applicable"]


class EvaluationIssue(BaseModel):
    id: UUID
    origin: Literal["rule", "judge"]
    dimension: EvaluationDimension
    severity: IssueSeverity
    title: str
    explanation: str
    script_quote: str | None = None
    script_start: int | None = None
    script_end: int | None = None
    article_evidence_quote: str | None = None
    evidence_start: int | None = None
    evidence_end: int | None = None
    evidence_status: EvidenceStatus


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    script_version_id: UUID
    job_id: UUID
    status: EvaluationStatus
    factual_consistency: float | None
    content_coverage: float | None
    audio_readiness: float | None
    rule_version: str
    provider: str | None
    requested_model: str | None
    actual_model: str | None
    prompt_version: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class EvaluationDetail(EvaluationSummary):
    issues: list[EvaluationIssue]


class EvaluationList(BaseModel):
    items: list[EvaluationSummary]
    total: int
    limit: int
    offset: int


class EvaluationCreateResult(BaseModel):
    job: JobOut
    evaluation: EvaluationSummary
    idempotent_replay: bool


class ScriptEditRequest(BaseModel):
    text: str = Field(min_length=100, max_length=30000)
    expected_version: int = Field(ge=1)

    @field_validator("text")
    @classmethod
    def validate_script_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 100:
            raise ValueError("Script text must contain at least 100 characters")
        return value


class ApprovalRequest(BaseModel):
    evaluation_id: UUID
    expected_version: int = Field(ge=1)
    acknowledged_critical_issue_ids: list[UUID] = Field(default_factory=list)

    @field_validator("acknowledged_critical_issue_ids")
    @classmethod
    def unique_issue_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Acknowledged issue IDs must be unique")
        return value


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    script_version_id: UUID
    evaluation_id: UUID
    acknowledged_critical_issue_ids: list[UUID]
    created_at: datetime


class ApprovalList(BaseModel):
    items: list[ApprovalOut]
