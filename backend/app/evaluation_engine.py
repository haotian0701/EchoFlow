import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings, get_settings
from app.llm import WORD_TARGETS, ProviderError, response_metadata
from app.models import Article, ScriptVersion

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


def _normalized_with_map(value: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    positions: list[int] = []
    in_space = False
    for index, character in enumerate(unicodedata.normalize("NFKC", value)):
        if character.isspace():
            if normalized and not in_space:
                normalized.append(" ")
                positions.append(index)
            in_space = True
        else:
            normalized.append(character.casefold())
            positions.append(index)
            in_space = False
    if normalized and normalized[-1] == " ":
        normalized.pop()
        positions.pop()
    return "".join(normalized), positions


def locate_quote(text: str, quote: str | None) -> tuple[int | None, int | None]:
    if not quote or not quote.strip():
        return None, None
    direct = text.find(quote)
    if direct >= 0:
        return direct, direct + len(quote)
    normalized_text, positions = _normalized_with_map(text)
    normalized_quote, _ = _normalized_with_map(quote)
    start = normalized_text.find(normalized_quote)
    if start < 0 or not positions:
        return None, None
    end_index = min(start + len(normalized_quote) - 1, len(positions) - 1)
    return positions[start], positions[end_index] + 1


def _issue(
    *,
    dimension: Dimension,
    severity: Severity,
    title: str,
    explanation: str,
    script_text: str,
    article_text: str,
    script_quote: str | None = None,
    article_quote: str | None = None,
    evidence_not_applicable: bool = False,
    origin: Literal["rule", "judge"] = "rule",
) -> dict:
    script_start, script_end = locate_quote(script_text, script_quote)
    evidence_start, evidence_end = locate_quote(article_text, article_quote)
    if evidence_not_applicable:
        evidence_status = "not_applicable"
    elif article_quote and evidence_start is not None:
        evidence_status = "verified"
    else:
        evidence_status = "insufficient"
    return {
        "id": str(uuid.uuid4()),
        "origin": origin,
        "dimension": dimension,
        "severity": severity,
        "title": title,
        "explanation": explanation,
        "script_quote": script_quote,
        "script_start": script_start,
        "script_end": script_end,
        "article_evidence_quote": article_quote,
        "evidence_start": evidence_start,
        "evidence_end": evidence_end,
        "evidence_status": evidence_status,
    }


def _number_tokens(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in re.finditer(r"(?<!\w)[+-]?\d[\d.,]*(?:\s?%)?", text):
        raw = match.group(0)
        key = re.sub(r"[\s,]", "", raw).casefold()
        result.setdefault(key, raw)
    return result


def run_rules(article: Article, script: ScriptVersion) -> list[dict]:
    issues: list[dict] = []
    text = script.text
    article_text = article.original_text
    word_count = len(re.findall(r"\S+", text))
    target = WORD_TARGETS[script.target_language][script.generation_config["length"]]
    deviation = abs(word_count - target) / target

    if len(text) < 100 or len(text) > 30000:
        issues.append(
            _issue(
                dimension="audio_readiness",
                severity="critical",
                title="Script length is outside supported limits",
                explanation="The script must contain between 100 and 30,000 characters.",
                script_text=text,
                article_text=article_text,
                evidence_not_applicable=True,
            )
        )
    if deviation > 0.20:
        severity: Severity = "critical" if deviation > 0.40 else "warning"
        issues.append(
            _issue(
                dimension="content_coverage",
                severity=severity,
                title="Script misses the configured length target",
                explanation=(
                    f"The script has {word_count} words; the configured target is about {target}. "
                    "Confirm that the source length justifies the difference."
                ),
                script_text=text,
                article_text=article_text,
                evidence_not_applicable=True,
            )
        )

    for sentence_match in re.finditer(r"[^.!?\n]+(?:[.!?]|$)", text):
        sentence = sentence_match.group(0).strip()
        words = len(re.findall(r"\S+", sentence))
        if words > 45:
            issues.append(
                _issue(
                    dimension="audio_readiness",
                    severity="critical" if words > 70 else "warning",
                    title="Sentence may be difficult to narrate",
                    explanation=f"This sentence contains {words} words; consider splitting it.",
                    script_text=text,
                    article_text=article_text,
                    script_quote=sentence,
                    evidence_not_applicable=True,
                )
            )

    format_match = re.search(
        r"(?:https?://\S+|^\s{0,3}(?:[-*#]|\d+[.)])\s+|`{1,3})", text, re.MULTILINE
    )
    if format_match:
        issues.append(
            _issue(
                dimension="audio_readiness",
                severity="warning",
                title="Written formatting may sound unnatural",
                explanation="Remove URLs, Markdown, or list markers before narration.",
                script_text=text,
                article_text=article_text,
                script_quote=format_match.group(0).strip(),
                evidence_not_applicable=True,
            )
        )

    article_numbers = _number_tokens(article_text)
    script_numbers = _number_tokens(text)
    for key, raw in script_numbers.items():
        if key not in article_numbers:
            issues.append(
                _issue(
                    dimension="factual_consistency",
                    severity="warning",
                    title="Number is not found verbatim in the source",
                    explanation=(
                        "Check whether this number is supported or is a valid language or unit conversion."
                    ),
                    script_text=text,
                    article_text=article_text,
                    script_quote=raw,
                )
            )
    for key, raw in article_numbers.items():
        if key not in script_numbers:
            issues.append(
                _issue(
                    dimension="content_coverage",
                    severity="info",
                    title="Source number is not present in the script",
                    explanation="Review whether this figure is material enough to include.",
                    script_text=text,
                    article_text=article_text,
                    article_quote=raw,
                )
            )
    return issues


def _judge_issue(article: Article, script: ScriptVersion, issue: JudgeIssue) -> dict:
    return _issue(
        dimension=issue.dimension,
        severity=issue.severity,
        title=issue.title,
        explanation=issue.explanation,
        script_text=script.text,
        article_text=article.original_text,
        script_quote=issue.script_quote,
        article_quote=issue.article_evidence_quote,
        evidence_not_applicable=issue.dimension == "audio_readiness",
        origin="judge",
    )


class MockEvaluationProvider:
    name = "mock"
    requested_model = "mock/evaluator-v1"

    async def evaluate_script(
        self,
        article: Article,
        script: ScriptVersion,
        rule_issues: list[dict],
        job_id: UUID,
    ) -> EvaluationResult:
        started = time.monotonic()
        scores = {
            "factual_consistency": 0.95,
            "content_coverage": 0.95,
            "audio_readiness": 0.95,
        }
        deductions = {"info": 0.03, "warning": 0.10, "critical": 0.25}
        for issue in rule_issues:
            dimension = issue["dimension"]
            scores[dimension] = max(
                0.0, scores[dimension] - deductions[issue["severity"]]
            )
        return EvaluationResult(
            factual_consistency=round(scores["factual_consistency"], 3),
            content_coverage=round(scores["content_coverage"], 3),
            audio_readiness=round(scores["audio_readiness"], 3),
            issues=[],
            provider=self.name,
            requested_model=self.requested_model,
            actual_model=self.requested_model,
            provider_request_id=f"mock-evaluation-{job_id}",
            latency_ms=round((time.monotonic() - started) * 1000),
        )


class OpenRouterEvaluationProvider:
    name = "openrouter"

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.settings = settings
        self.requested_model = settings.openrouter_evaluation_model
        self.transport = transport

    async def evaluate_script(
        self,
        article: Article,
        script: ScriptVersion,
        rule_issues: list[dict],
        job_id: UUID,
    ) -> EvaluationResult:
        system = (
            "You are a cautious editor evaluating an audio script against its only source article. "
            "Score factual consistency, content coverage, and audio readiness from 0 to 1. "
            "Report only material, reviewable issues. Never invent source evidence. Return JSON only."
        )
        user = (
            f"Target language: {script.target_language}\n\n"
            f"ARTICLE\n{article.original_text}\n\nSCRIPT\n{script.text}"
        )
        issue_schema = {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "enum": [
                        "factual_consistency",
                        "content_coverage",
                        "audio_readiness",
                    ],
                },
                "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                "title": {"type": "string", "minLength": 1, "maxLength": 160},
                "explanation": {"type": "string", "minLength": 1, "maxLength": 1200},
                "script_quote": {"type": ["string", "null"], "maxLength": 1000},
                "article_evidence_quote": {
                    "type": ["string", "null"],
                    "maxLength": 1000,
                },
            },
            "required": [
                "dimension",
                "severity",
                "title",
                "explanation",
                "script_quote",
                "article_evidence_quote",
            ],
            "additionalProperties": False,
        }
        payload = {
            "model": self.requested_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.settings.evaluation_temperature,
            "max_completion_tokens": self.settings.evaluation_tokens,
            "reasoning_effort": self.settings.evaluation_reasoning_effort,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "script_evaluation",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "factual_consistency": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "content_coverage": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "audio_readiness": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "issues": {
                                "type": "array",
                                "maxItems": 50,
                                "items": issue_schema,
                            },
                        },
                        "required": [
                            "factual_consistency",
                            "content_coverage",
                            "audio_readiness",
                            "issues",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "HTTP-Referer": self.settings.openrouter_http_referer,
            "X-Title": self.settings.openrouter_app_title,
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.openrouter_base_url,
                timeout=self.settings.openrouter_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    "/chat/completions", headers=headers, json=payload
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderError(
                "PROVIDER_UNAVAILABLE",
                "Could not connect to OpenRouter",
                retryable=True,
            ) from exc
        except (
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.RemoteProtocolError,
        ) as exc:
            raise ProviderError(
                "PROVIDER_RESULT_UNKNOWN",
                "OpenRouter did not return a definite evaluation result",
                ambiguous=True,
            ) from exc

        metadata = response_metadata(response, started)
        if response.status_code >= 400:
            message = "OpenRouter rejected the evaluation request"
            if response.status_code in {429, 503, 529}:
                raise ProviderError(
                    "PROVIDER_TEMPORARY_ERROR",
                    message,
                    retryable=True,
                    metadata=metadata,
                )
            if response.status_code in {408, 500, 502, 524}:
                raise ProviderError(
                    "PROVIDER_RESULT_UNKNOWN",
                    message,
                    ambiguous=True,
                    metadata=metadata,
                )
            raise ProviderError("PROVIDER_REJECTED", message, metadata=metadata)

        try:
            data = response.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ProviderError(
                    "LLM_OUTPUT_TRUNCATED", "The evaluation was truncated"
                )
            parsed = JudgePayload.model_validate_json(choice["message"]["content"])
        except ProviderError as error:
            error.metadata = metadata
            raise
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise ProviderError(
                "INVALID_PROVIDER_RESPONSE",
                "OpenRouter returned an invalid evaluation",
                metadata=metadata,
            ) from exc

        return EvaluationResult(
            factual_consistency=round(parsed.factual_consistency, 3),
            content_coverage=round(parsed.content_coverage, 3),
            audio_readiness=round(parsed.audio_readiness, 3),
            issues=[_judge_issue(article, script, issue) for issue in parsed.issues],
            provider=self.name,
            requested_model=self.requested_model,
            actual_model=metadata.get("actual_model") or self.requested_model,
            provider_request_id=metadata.get("provider_request_id"),
            prompt_tokens=metadata.get("prompt_tokens"),
            completion_tokens=metadata.get("completion_tokens"),
            total_tokens=metadata.get("total_tokens"),
            cost=metadata.get("cost"),
            latency_ms=round((time.monotonic() - started) * 1000),
        )


def get_evaluation_provider(settings=None) -> EvaluationProvider:
    settings = settings or get_settings()
    if settings.llm_provider == "mock":
        return MockEvaluationProvider()
    if settings.llm_provider == "openrouter":
        if not settings.openrouter_api_key:
            raise ProviderError(
                "LLM_NOT_CONFIGURED", "OpenRouter API key is not configured"
            )
        return OpenRouterEvaluationProvider(settings)
    raise ProviderError(
        "LLM_PROVIDER_INVALID", f"Unsupported LLM provider: {settings.llm_provider}"
    )
