from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.articles import Article
from app.models.scripts import ScriptVersion

RULE_VERSION = "evaluation-rules-v1"


EVALUATION_PROMPT_VERSION = "script-evaluation-v1"


Dimension = Literal["factual_consistency", "content_coverage", "audio_readiness"]


Severity = Literal["info", "warning", "critical"]


class JudgeIssue(BaseModel):
    dimension: Dimension
    severity: Severity
    title: str = Field(min_length=1, max_length=160)
    explanation: str = Field(min_length=1, max_length=1200)
    script_quote: str | None = Field(default=None, max_length=1000)
    article_evidence_quote: str | None = Field(default=None, max_length=1000)


class JudgePayload(BaseModel):
    factual_consistency: float = Field(ge=0, le=1)
    content_coverage: float = Field(ge=0, le=1)
    audio_readiness: float = Field(ge=0, le=1)
    issues: list[JudgeIssue] = Field(max_length=50)


@dataclass
class EvaluationResult:
    factual_consistency: float
    content_coverage: float
    audio_readiness: float
    issues: list[dict]
    provider: str
    requested_model: str
    actual_model: str
    provider_request_id: str | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None
    latency_ms: int | None = None


class EvaluationProvider(Protocol):
    name: str
    requested_model: str

    async def evaluate_script(
        self,
        article: Article,
        script: ScriptVersion,
        rule_issues: list[dict],
        job_id: UUID,
    ) -> EvaluationResult: ...
