import json
import uuid

import httpx
import pytest

from app.config import Settings
from app.llm import OpenRouterProvider, ProviderError, estimated_duration_seconds
from app.models import Article
from app.schemas import ScriptGenerationConfig


def article():
    return Article(
        title="Quarterly report",
        source="Newsroom",
        original_text=("Revenue grew by 12 percent in Berlin. " * 6).strip(),
        language="en",
        import_type="manual",
        content_hash="x" * 64,
    )


def config():
    return ScriptGenerationConfig(
        target_language="en", length="short", style="news", audience="business"
    )


@pytest.mark.asyncio
async def test_openrouter_structured_success():
    script = ("Welcome to the briefing. Revenue grew by 12 percent in Berlin. " * 3).strip()

    def handler(request):
        sent = json.loads(request.content)
        assert sent["response_format"]["type"] == "json_schema"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "model": "google/gemini-2.5-flash-actual",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"script": script})}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50, "cost": 0.001},
            },
        )

    settings = Settings(openrouter_api_key="secret", openrouter_model="google/gemini-2.5-flash")
    provider = OpenRouterProvider(settings, transport=httpx.MockTransport(handler))
    result = await provider.generate_script(article(), config(), uuid.uuid4())
    assert result.text == script
    assert result.actual_model.endswith("-actual")
    assert result.total_tokens == 50
    assert result.cost == 0.001


@pytest.mark.asyncio
@pytest.mark.parametrize("status,retryable,ambiguous", [(429, True, False), (503, True, False), (529, True, False), (401, False, False), (502, False, True)])
async def test_openrouter_error_classification(status, retryable, ambiguous):
    provider = OpenRouterProvider(
        Settings(openrouter_api_key="secret"),
        transport=httpx.MockTransport(lambda _request: httpx.Response(status, json={"error": {"message": "provider error"}})),
    )
    with pytest.raises(ProviderError) as caught:
        await provider.generate_script(article(), config(), uuid.uuid4())
    assert caught.value.retryable is retryable
    assert caught.value.ambiguous is ambiguous


def test_estimated_duration_uses_target_language_rate():
    text = " ".join(["word"] * 150)
    assert estimated_duration_seconds(text, "en") == 60
    assert estimated_duration_seconds(text, "de") == 69
