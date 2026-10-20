import json
import re
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings, get_settings
from app.models import Article
from app.schemas import ScriptGenerationConfig

PROMPT_VERSION = "script-generation-v1"
WORD_TARGETS = {
    "en": {"short": 300, "medium": 750, "long": 1350},
    "de": {"short": 260, "medium": 650, "long": 1170},
}
COMPLETION_TOKEN_BUDGETS = {
    "short": 4000,
    "medium": 6000,
    "long": 8000,
}
WORDS_PER_MINUTE = {"en": 150, "de": 130}


class ScriptPayload(BaseModel):
    script: str = Field(min_length=100, max_length=30000)


@dataclass
class GenerationResult:
    text: str
    provider: str
    requested_model: str
    actual_model: str
    provider_request_id: str | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None
    latency_ms: int | None = None


class ProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, ambiguous: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.ambiguous = ambiguous


class LLMProvider(Protocol):
    name: str
    requested_model: str

    async def generate_script(
        self, article: Article, config: ScriptGenerationConfig, job_id: UUID
    ) -> GenerationResult: ...


def estimated_duration_seconds(text: str, language: str) -> int:
    words = len(re.findall(r"\S+", text))
    return max(1, round(words / WORDS_PER_MINUTE[language] * 60))


def build_prompt(article: Article, config: ScriptGenerationConfig) -> tuple[str, str]:
    target = WORD_TARGETS[config.target_language][config.length]
    language = "English" if config.target_language == "en" else "German"
    style = {
        "news": "clear, factual news narration",
        "conversational": "warm, natural conversational narration",
        "professional": "formal, concise professional narration",
    }[config.style]
    audience = {
        "general": "a general audience; explain necessary jargon plainly",
        "business": "a business audience; retain material commercial and organizational detail",
    }[config.audience]
    system = (
        "You are an audio script editor. Use only facts supported by the supplied article. "
        "Preserve names, organizations, dates, quantities, and causal relationships. "
        "Do not add background facts, opinions, citations, stage directions, or markdown. "
        "Create a natural opening and closing and return only the requested JSON object."
    )
    user = (
        f"Write a {style} script in {language} for {audience}. "
        f"Aim for about {target} words (within 20 percent when the source permits).\n\n"
        f"Article title: {article.title}\nSource: {article.source}\n"
        f"Original language: {article.language}\n\nArticle:\n{article.original_text}"
    )
    return system, user


class MockProvider:
    name = "mock"
    requested_model = "mock/script-v1"

    async def generate_script(
        self, article: Article, config: ScriptGenerationConfig, job_id: UUID
    ) -> GenerationResult:
        started = time.monotonic()
        if config.target_language == "de":
            opening = f"Willkommen. Heute geht es um {article.title}."
            closing = "Damit endet dieser Überblick. Danke fürs Zuhören."
        else:
            opening = f"Welcome. Today we are looking at {article.title}."
            closing = "That concludes this briefing. Thank you for listening."
        target = WORD_TARGETS[config.target_language][config.length]
        source_words = article.original_text.split()
        body = " ".join(source_words[: max(20, target - 20)])
        text = f"{opening}\n\n{body}\n\n{closing}"
        if len(text) < 100:
            text = f"{text}\n\n{article.original_text}"
        return GenerationResult(
            text=text[:30000],
            provider=self.name,
            requested_model=self.requested_model,
            actual_model=self.requested_model,
            provider_request_id=f"mock-{job_id}",
            latency_ms=round((time.monotonic() - started) * 1000),
        )


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.requested_model = settings.openrouter_model
        self.transport = transport

    async def generate_script(
        self, article: Article, config: ScriptGenerationConfig, job_id: UUID
    ) -> GenerationResult:
        system, user = build_prompt(article, config)
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "HTTP-Referer": self.settings.openrouter_http_referer,
            "X-Title": self.settings.openrouter_app_title,
        }
        payload = {
            "model": self.requested_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            # The completion budget also includes hidden reasoning tokens for
            # reasoning-capable models. Keep a bounded reserve so a short
            # script is not cut off before its structured JSON is emitted.
            "max_completion_tokens": COMPLETION_TOKEN_BUDGETS[config.length],
            "reasoning_effort": "low",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "audio_script",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"script": {"type": "string", "minLength": 100, "maxLength": 30000}},
                        "required": ["script"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.openrouter_base_url,
                timeout=self.settings.openrouter_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post("/chat/completions", headers=headers, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE", "Could not connect to OpenRouter", retryable=True) from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            raise ProviderError("PROVIDER_RESULT_UNKNOWN", "OpenRouter did not return a definite result", ambiguous=True) from exc

        if response.status_code >= 400:
            message = "OpenRouter rejected the generation request"
            try:
                error = response.json().get("error", {})
                if isinstance(error, dict) and error.get("message"):
                    message = str(error["message"])
            except (ValueError, AttributeError):
                pass
            if response.status_code in {429, 503, 529}:
                raise ProviderError("PROVIDER_TEMPORARY_ERROR", message, retryable=True)
            if response.status_code in {408, 500, 502, 524}:
                raise ProviderError("PROVIDER_RESULT_UNKNOWN", message, ambiguous=True)
            raise ProviderError("PROVIDER_REJECTED", message)

        try:
            data = response.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ProviderError("LLM_OUTPUT_TRUNCATED", "The generated script was truncated")
            content = choice["message"]["content"]
            parsed = ScriptPayload.model_validate(json.loads(content))
        except ProviderError:
            raise
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise ProviderError("INVALID_PROVIDER_RESPONSE", "OpenRouter returned an invalid script response") from exc

        usage = data.get("usage") or {}
        return GenerationResult(
            text=parsed.script,
            provider=self.name,
            requested_model=self.requested_model,
            actual_model=str(data.get("model") or self.requested_model),
            provider_request_id=data.get("id"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            cost=usage.get("cost"),
            latency_ms=round((time.monotonic() - started) * 1000),
        )


def get_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "mock":
        return MockProvider()
    if settings.llm_provider == "openrouter":
        if not settings.openrouter_api_key:
            raise ProviderError("LLM_NOT_CONFIGURED", "OpenRouter API key is not configured")
        return OpenRouterProvider(settings)
    raise ProviderError("LLM_PROVIDER_INVALID", f"Unsupported LLM provider: {settings.llm_provider}")
