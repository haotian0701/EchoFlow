from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import JobStatus
from app.schemas.scripts import ScriptGenerationConfig


class JobCreate(BaseModel):
    type: Literal["script_generation"]
    article_id: UUID
    config: ScriptGenerationConfig


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    retry_of_job_id: UUID | None = None
    root_job_id: UUID | None = None
    current_execution_id: UUID | None = None
    provider_snapshot: dict[str, Any] = Field(default_factory=dict)
    article_title: str = ""
    can_retry: bool = False
    retry_mode: Literal["new_call", "resume_upload"] = "new_call"
    requires_charge_acknowledgement: bool = False
    retry_child_job_id: UUID | None = None
    retry_unavailable_reason: str | None = None
    recovery_expires_at: datetime | None = None
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
    total: int = 0
    limit: int = 100
    offset: int = 0


class JobRetryBody(BaseModel):
    acknowledge_possible_charge: bool = False


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    job_id: UUID
    attempt_no: int
    stage: str
    status: str
    heartbeat_at: datetime
    lease_expires_at: datetime
    started_at: datetime
    completed_at: datetime | None


class ProviderCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    job_id: UUID
    execution_id: UUID | None
    operation: str
    provider: str
    requested_model: str
    actual_model: str | None
    provider_request_id: str | None
    status: str
    retryable: bool
    uncertain: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost: float | None
    provider_credits: float | None
    latency_ms: int | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
