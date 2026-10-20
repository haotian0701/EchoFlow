from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.jobs import JobOut

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
