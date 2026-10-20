import uuid

import pytest

from app.core.config import Settings
from app.models.jobs import ProviderCall
from app.services.execution import claim
from app.services.recovery import recover_once
from app.services.script_production import (
    process_generation_job,
)
from tests.factories import (
    expire,
    failed_generation,
    seed_job,
)
from tests.fakes import SuccessfulProvider


@pytest.mark.asyncio
async def test_metrics_units_coverage_retry_leaves_and_time_range(sessions, client):
    parent = await failed_generation(sessions, True)
    child = (
        await client.post(
            f"/jobs/{parent}/retry", json={"acknowledge_possible_charge": True}
        )
    ).json()["job"]
    await process_generation_job(
        uuid.UUID(child["id"]),
        session_factory=sessions,
        provider=SuccessfulProvider(),
    )
    async with sessions() as session:
        session.add_all(
            [
                ProviderCall(
                    job_id=parent,
                    operation="script_generation",
                    provider="openrouter",
                    requested_model="glm",
                    status="failed",
                    cost=0.12,
                    latency_ms=1200,
                ),
                ProviderCall(
                    job_id=parent,
                    operation="audio_generation",
                    provider="elevenlabs",
                    requested_model="flash",
                    status="succeeded",
                    provider_credits=100,
                    latency_ms=2500,
                ),
                ProviderCall(
                    job_id=parent,
                    operation="script_evaluation",
                    provider="openrouter",
                    requested_model="glm",
                    status="unknown",
                ),
            ]
        )
        await session.commit()
    metrics = (await client.get("/metrics/overview")).json()
    assert metrics["total_jobs"] == 2 and metrics["manual_retry_count"] == 1
    assert metrics["unresolved_needs_attention_count"] == 0
    assert (
        metrics["usage"]["openrouter_usd"] == 0.12
        and metrics["usage"]["elevenlabs_credits"] == 100
    )
    assert metrics["active_processing"]["sample_count"] == 2
    assert (
        await client.get(
            "/metrics/overview?from=2000-01-01T00:00:00Z&to=2001-01-01T00:00:00Z"
        )
    ).json()["total_jobs"] == 0
    assert (
        await client.get("/metrics/overview?from=2030-01-01&to=2000-01-01")
    ).status_code == 422


@pytest.mark.asyncio
async def test_event_call_execution_pagination_and_empty_metrics(sessions, client):
    empty = (await client.get("/metrics/overview?all_time=true")).json()
    assert empty["total_jobs"] == 0 and empty["success_rate"] is None
    job_id = await failed_generation(sessions)
    for route in ("events", "calls", "executions"):
        response = await client.get(f"/jobs/{job_id}/{route}?limit=1&offset=0")
        assert response.status_code == 200
        page = response.json()
        assert page["total"] >= 1 and len(page["items"]) == 1
        assert (await client.get(f"/jobs/{job_id}/{route}?limit=0")).status_code == 422
    assert (
        await client.post(f"/jobs/{uuid.uuid4()}/retry", json={})
    ).status_code == 404


@pytest.mark.asyncio
async def test_abandoned_duration_excluded_and_demo_refuses_paid_configuration(
    sessions, client, monkeypatch
):
    _, job_id = await seed_job(sessions)
    execution_id = await claim(sessions, job_id, "script_generation")
    await expire(sessions, execution_id)
    await recover_once(sessions)
    await process_generation_job(
        job_id, session_factory=sessions, provider=SuccessfulProvider()
    )
    metric = (await client.get("/metrics/overview")).json()["active_processing"]
    assert (
        metric["sample_count"] == 0
        and metric["average_seconds"] is None
        and metric["terminal_job_count"] == 1
    )
    from app import demo

    monkeypatch.setattr(
        demo,
        "get_settings",
        lambda: Settings(_env_file=None, llm_provider="openrouter"),
    )
    with pytest.raises(SystemExit, match="Demo refused"):
        await demo.run()
