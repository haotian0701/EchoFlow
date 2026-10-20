from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.articles import ArticleSummary
from app.schemas.common import Language
from app.schemas.jobs import JobOut


class AudioCreate(BaseModel):
    language: Language
    voice_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    speed: float = Field(
        default=1.0, ge=0.7, le=1.2, multiple_of=0.01, allow_inf_nan=False
    )


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
