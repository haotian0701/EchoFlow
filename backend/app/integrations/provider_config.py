"""Whitelisted non-secret snapshots; credentials always come from current settings."""

from app.core.config import get_settings

LLM_FIELDS = (
    "llm_provider",
    "openrouter_model",
    "openrouter_evaluation_model",
    "openrouter_base_url",
    "openrouter_timeout_seconds",
    "openrouter_http_referer",
    "openrouter_app_title",
    "generation_tokens_short",
    "generation_tokens_medium",
    "generation_tokens_long",
    "evaluation_tokens",
    "generation_reasoning_effort",
    "evaluation_reasoning_effort",
    "generation_temperature",
    "evaluation_temperature",
)
TTS_FIELDS = (
    "tts_provider",
    "elevenlabs_model",
    "elevenlabs_base_url",
    "tts_timeout_seconds",
    "tts_max_response_bytes",
)


def snapshot(operation, settings=None):
    settings = settings or get_settings()
    return {
        key: getattr(settings, key)
        for key in (TTS_FIELDS if operation == "audio_generation" else LLM_FIELDS)
    }


def execution_settings(saved):
    allowed = set(LLM_FIELDS + TTS_FIELDS)
    return get_settings().model_copy(
        update={k: v for k, v in (saved or {}).items() if k in allowed}
    )
