import json
import uuid

import pytest

from app.core.config import Settings
from app.services.script_production import (
    process_evaluation_job,
    process_generation_job,
)
from tests.factories import approved_script, seed_job


@pytest.mark.asyncio
@pytest.mark.parametrize("evaluation_mode", [False, True])
async def test_truncated_responses_preserve_safe_model_usage_and_cost(
    sessions, client, evaluation_mode
):
    import httpx

    from app.integrations.llm.evaluation import OpenRouterEvaluationProvider
    from app.integrations.llm.generation import OpenRouterProvider

    settings = Settings(
        _env_file=None, llm_provider="openrouter", openrouter_api_key="test-key"
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "id": "gen-accounted",
                "model": "actual/glm",
                "usage": {
                    "prompt_tokens": 22,
                    "completion_tokens": 33,
                    "total_tokens": 55,
                    "cost": 0.002,
                },
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "unsafe raw content"},
                    }
                ],
            },
        )
    )
    if evaluation_mode:
        _, script = await approved_script(client, sessions)
        response = await client.post(f"/scripts/{script.id}/evaluate")
        job_id = uuid.UUID(response.json()["job"]["id"])
        await process_evaluation_job(
            job_id,
            session_factory=sessions,
            provider=OpenRouterEvaluationProvider(settings, transport),
        )
    else:
        _, job_id = await seed_job(sessions)
        await process_generation_job(
            job_id,
            session_factory=sessions,
            provider=OpenRouterProvider(settings, transport),
        )
    result = (await client.get(f"/jobs/{job_id}/calls")).json()["items"][0]
    assert result["status"] == "failed" and result["actual_model"] == "actual/glm"
    assert result["total_tokens"] == 55 and result["cost"] == 0.002
    assert (
        result["provider_request_id"] == "gen-accounted"
        and result["latency_ms"] is not None
    )
    assert "unsafe" not in json.dumps(result) and "test-key" not in json.dumps(result)
