import time
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.provider_errors import ProviderError
from app.evaluation.contracts import EvaluationProvider, EvaluationResult, JudgePayload
from app.evaluation.evidence import _judge_issue
from app.integrations.llm.common import response_metadata
from app.models.articles import Article
from app.models.scripts import ScriptVersion


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
