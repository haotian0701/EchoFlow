import uuid

import httpx
import pytest

from app.core.config import Settings
from app.core.provider_errors import ProviderError
from app.evaluation.rules import run_rules
from app.integrations.llm.evaluation import (
    MockEvaluationProvider,
    OpenRouterEvaluationProvider,
)
from app.models.articles import Article
from app.models.scripts import ScriptVersion


def records(script_text: str, article_text: str | None = None):
    article = Article(
        title="Report",
        source="Newsroom",
        original_text=article_text or ("Revenue rose by 12 percent in Berlin. " * 15),
        language="en",
        import_type="manual",
        content_hash="a" * 64,
    )
    script = ScriptVersion(
        article_id=uuid.uuid4(),
        version=1,
        text=script_text,
        source="generated",
        target_language="en",
        generation_config={
            "target_language": "en",
            "length": "short",
            "style": "news",
            "audience": "general",
        },
        provider="mock",
        requested_model="mock/script-v1",
        actual_model="mock/script-v1",
        prompt_version="script-generation-v1",
        estimated_duration_seconds=120,
    )
    return article, script


@pytest.mark.asyncio
async def test_mock_evaluator_deducts_rule_scores():
    article, script = records("Revenue rose by 99 percent. " * 12)
    issues = run_rules(article, script)
    result = await MockEvaluationProvider().evaluate_script(
        article, script, issues, uuid.uuid4()
    )
    assert result.factual_consistency < 0.95
    assert result.actual_model == "mock/evaluator-v1"


@pytest.mark.asyncio
async def test_openrouter_evaluator_validates_and_locates_output():
    article, script = records("Revenue rose by 12 percent in Berlin. " * 10)

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "id": "generation-1",
                "model": "judge/model-v2",
                "usage": {"total_tokens": 80},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"factual_consistency":0.9,"content_coverage":0.8,'
                                '"audio_readiness":0.7,"issues":[{"dimension":'
                                '"factual_consistency","severity":"warning","title":'
                                '"Check the figure","explanation":"Verify it.",'
                                '"script_quote":"12 percent","article_evidence_quote":'
                                '"12 percent"}]}'
                            )
                        },
                    }
                ],
            },
        )

    settings = Settings(
        llm_provider="openrouter",
        openrouter_api_key="test-key",
        openrouter_evaluation_model="judge/model",
    )
    provider = OpenRouterEvaluationProvider(
        settings, transport=httpx.MockTransport(handler)
    )
    result = await provider.evaluate_script(article, script, [], uuid.uuid4())
    assert result.actual_model == "judge/model-v2"
    assert result.issues[0]["evidence_status"] == "verified"
    assert result.issues[0]["script_start"] is not None


@pytest.mark.asyncio
async def test_openrouter_evaluator_classifies_temporary_error():
    article, script = records("Revenue rose by 12 percent. " * 12)
    provider = OpenRouterEvaluationProvider(
        Settings(llm_provider="openrouter", openrouter_api_key="test-key"),
        transport=httpx.MockTransport(lambda request: httpx.Response(529, json={})),
    )
    with pytest.raises(ProviderError) as caught:
        await provider.evaluate_script(article, script, [], uuid.uuid4())
    assert caught.value.retryable is True
