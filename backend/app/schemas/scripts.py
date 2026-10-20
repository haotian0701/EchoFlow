from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import ScriptAudience, ScriptLength, ScriptStyle, TargetLanguage


class ScriptGenerationConfig(BaseModel):
    target_language: TargetLanguage
    length: ScriptLength
    style: ScriptStyle
    audience: ScriptAudience


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
