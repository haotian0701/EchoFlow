import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.audio_staging import paths, verify
from app.audio_worker import process_audio_job
from app.config import Settings
from app.dispatcher import dispatch_once
from app.execution import claim, fenced
from app.logging_config import SafeJsonFormatter
from app.maintenance import recover_once, storage_once
from app.models import (
    AudioArtifact,
    AudioAsset,
    Evaluation,
    JobExecution,
    ProductionJob,
    ProviderCall,
    TaskOutbox,
)
from app.worker import process_evaluation_job, process_generation_job
from tests.test_audio import MemoryStorage, approved_script, queued_job
from tests.test_worker import FailingProvider, SuccessfulProvider, seed_job


@pytest.fixture(autouse=True)
def mock_config(monkeypatch, tmp_path):
    settings = Settings(
        _env_file=None,
        audio_staging_path=str(tmp_path),
        llm_provider="mock",
        tts_provider="mock",
    )
    for module in (
        "config",
        "provider_config",
        "execution",
        "audio_staging",
        "audio_worker",
        "maintenance",
        "tts",
        "job_service",
        "review_service",
    ):
        monkeypatch.setattr(f"app.{module}.get_settings", lambda: settings)
    return settings


async def failed_generation(client, ambiguous=False):
    _, job_id = await seed_job(client.test_sessions)
    await process_generation_job(
        job_id,
        session_factory=client.test_sessions,
        provider=FailingProvider(ambiguous=ambiguous),
    )
    return job_id


async def expire(client, execution_id):
    async with client.test_sessions() as session:
        execution = await session.get(JobExecution, execution_id)
        execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True])
async def test_expired_lease_classifies_evidence_and_fences_late_worker(
    client, prepared
):
    _, job_id = await seed_job(client.test_sessions)
    execution_id = await claim(client.test_sessions, job_id, "script_generation")
    assert await claim(client.test_sessions, job_id, "script_generation") is None
    if prepared:
        async with client.test_sessions() as session:
            session.add(
                ProviderCall(
                    job_id=job_id,
                    execution_id=execution_id,
                    provider="mock",
                    requested_model="mock/script-v1",
                    status="running",
                )
            )
            await session.commit()
    await expire(client, execution_id)
    async with client.test_sessions() as session:
        assert await fenced(session, job_id, execution_id) is None
    await recover_once(client.test_sessions)
    result = (await client.get(f"/jobs/{job_id}")).json()
    assert result["status"] == ("needs_attention" if prepared else "queued")
    executions = (await client.get(f"/jobs/{job_id}/executions")).json()
    assert executions["items"][0]["status"] == "abandoned"
    if prepared:
        assert (await client.get(f"/jobs/{job_id}/calls")).json()["items"][0][
            "status"
        ] == "unknown"
    else:
        new_execution = await claim(client.test_sessions, job_id, "script_generation")
        assert new_execution != execution_id
        async with client.test_sessions() as session:
            assert await fenced(session, job_id, execution_id) is None


@pytest.mark.asyncio
async def test_outbox_commits_runnable_state_before_publish_and_retains_broker_failure(
    client,
):
    _, job_id = await seed_job(client.test_sessions)
    async with client.test_sessions() as session:
        job = await session.get(ProductionJob, job_id)
        job.status = "retry_scheduled"
        row = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        row.status = "pending"
        await session.commit()
    sent = []

    def failure(*args, **kwargs):
        sent.append(kwargs)
        raise OSError("secret upstream URL must not be retained")

    assert await dispatch_once(client.test_sessions, failure) == 0
    async with client.test_sessions() as session:
        assert (await session.get(ProductionJob, job_id)).status == "queued"
        row = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        assert row.status == "pending" and "secret" not in row.last_error
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert (
        await dispatch_once(client.test_sessions, lambda *a, **kw: sent.append(kw)) == 1
    )
    async with client.test_sessions() as session:
        row = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        row.dispatched_at = datetime.now(UTC) - timedelta(seconds=121)
        await session.commit()
    await recover_once(client.test_sessions)
    async with client.test_sessions() as session:
        assert (
            await session.scalar(select(TaskOutbox).where(TaskOutbox.job_id == job_id))
        ).status == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_manual_retry_idempotency_charge_ack_and_linear_history(
    client, ambiguous
):
    job_id = await failed_generation(client, ambiguous)
    key = str(uuid.uuid4())
    url = f"/jobs/{job_id}/retry"
    body = {"acknowledge_possible_charge": False}
    if ambiguous:
        result = await client.post(url, json=body, headers={"Idempotency-Key": key})
        assert (
            result.status_code == 409
            and result.json()["code"] == "CHARGE_ACKNOWLEDGEMENT_REQUIRED"
        )
        body["acknowledge_possible_charge"] = True
    first = await client.post(url, json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    child = first.json()["job"]
    assert child["retry_of_job_id"] == str(job_id) and child["attempt_count"] == 0
    assert (
        child["root_job_id"] == str(job_id)
        and child["provider_snapshot"]["llm_provider"] == "mock"
    )
    for other_key in (key, str(uuid.uuid4())):
        replay = await client.post(
            url, json=body, headers={"Idempotency-Key": other_key}
        )
        assert replay.status_code == 200 and replay.json()["job"]["id"] == child["id"]
    conflict = await client.post(
        url,
        json={"acknowledge_possible_charge": not body["acknowledge_possible_charge"]},
        headers={"Idempotency-Key": key},
    )
    assert (
        conflict.status_code == 409
        and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    )
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == (
        "needs_attention" if ambiguous else "failed"
    )
    await process_generation_job(
        uuid.UUID(child["id"]),
        session_factory=client.test_sessions,
        provider=FailingProvider(),
    )
    grandchild = (await client.post(f"/jobs/{child['id']}/retry", json={})).json()[
        "job"
    ]
    assert grandchild["retry_of_job_id"] == child["id"] and grandchild[
        "root_job_id"
    ] == str(job_id)


@pytest.mark.asyncio
async def test_evaluation_retry_creates_new_report_and_rejects_stale_attempt(client):
    _, script = await approved_script(client)
    request = await client.post(f"/scripts/{script.id}/evaluate")
    job_id = uuid.UUID(request.json()["job"]["id"])

    class Failure:
        name = "mock"
        requested_model = "mock/evaluator-v1"

        async def evaluate_script(self, *args):
            from app.llm import ProviderError

            raise ProviderError("JUDGE_FAILED", "Invalid judge output")

    await process_evaluation_job(
        job_id, session_factory=client.test_sessions, provider=Failure()
    )
    retried = await client.post(f"/jobs/{job_id}/retry", json={})
    assert retried.status_code == 202, retried.text
    assert (
        retried.json()["job"]["result_evaluation_id"]
        != request.json()["evaluation"]["id"]
    )
    async with client.test_sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Evaluation)
                .where(Evaluation.script_version_id == script.id)
            )
            == 3
        )


@pytest.mark.asyncio
async def test_audio_upload_retry_reuses_checkpoint_call_and_historical_approval(
    client, monkeypatch
):
    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    storage = MemoryStorage()
    storage.fail_upload = True

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.audio_worker.asyncio.sleep", no_sleep)
    await process_audio_job(
        job_id, session_factory=client.test_sessions, storage=storage
    )
    async with client.test_sessions() as session:
        artifact = await session.scalar(
            select(AudioArtifact).where(AudioArtifact.job_id == job_id)
        )
        assert verify(artifact) and artifact.status == "ready"
    # Editing invalidates current approval but cannot invalidate already synthesized audio.
    edited = await client.patch(
        f"/scripts/{script.id}",
        json={
            "expected_version": 1,
            "text": script.text + " Additional editorial context.",
        },
    )
    assert edited.status_code == 201
    retried = await client.post(f"/jobs/{job_id}/retry", json={})
    assert (
        retried.status_code == 202 and retried.json()["retry_mode"] == "resume_upload"
    ), retried.text
    child_id = uuid.UUID(retried.json()["job"]["id"])
    # Automatic recovery must not restart a parent that already has a manual retry child.
    await recover_once(client.test_sessions)
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == "needs_attention"
    storage.fail_upload = False
    await process_audio_job(
        child_id, session_factory=client.test_sessions, storage=storage
    )
    async with client.test_sessions() as session:
        child = await session.get(ProductionJob, child_id)
        assert child.status == "succeeded"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProviderCall)
                .where(ProviderCall.operation == "audio_generation")
            )
            == 1
        )
        asset = await session.get(AudioAsset, child.result_audio_asset_id)
        assert asset.script_version_id == script.id
        assert not paths(artifact)[0].exists()


@pytest.mark.asyncio
async def test_checkpoint_recovery_when_db_ready_update_was_lost(client, monkeypatch):
    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    store = MemoryStorage()
    store.fail_upload = True

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.audio_worker.asyncio.sleep", no_sleep)
    await process_audio_job(job_id, session_factory=client.test_sessions, storage=store)
    async with client.test_sessions() as session:
        job = await session.get(ProductionJob, job_id)
        job.status = "running"
        execution = await session.get(JobExecution, job.current_execution_id)
        execution.status = "running"
        execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        artifact = await session.scalar(
            select(AudioArtifact).where(AudioArtifact.job_id == job_id)
        )
        artifact.status, artifact.sha256 = "preparing", None
        call = await session.get(ProviderCall, artifact.provider_call_id)
        call.status = "running"
        await session.commit()
    await recover_once(client.test_sessions)
    store.fail_upload = False
    await process_audio_job(job_id, session_factory=client.test_sessions, storage=store)
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_checkpoint_tampering_and_expiration_require_charge_ack(
    client, monkeypatch
):
    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    store = MemoryStorage()
    store.fail_upload = True

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.audio_worker.asyncio.sleep", no_sleep)
    await process_audio_job(job_id, session_factory=client.test_sessions, storage=store)
    async with client.test_sessions() as session:
        artifact = await session.scalar(
            select(AudioArtifact).where(AudioArtifact.job_id == job_id)
        )
        path, _ = paths(artifact)
        path.write_bytes(b"damaged")
        assert verify(artifact) is None
        artifact.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    result = await client.post(f"/jobs/{job_id}/retry", json={})
    assert (
        result.status_code == 409
        and result.json()["code"] == "CHARGE_ACKNOWLEDGEMENT_REQUIRED"
    )


@pytest.mark.asyncio
async def test_metrics_units_coverage_retry_leaves_and_time_range(client):
    parent = await failed_generation(client, True)
    child = (
        await client.post(
            f"/jobs/{parent}/retry", json={"acknowledge_possible_charge": True}
        )
    ).json()["job"]
    await process_generation_job(
        uuid.UUID(child["id"]),
        session_factory=client.test_sessions,
        provider=SuccessfulProvider(),
    )
    async with client.test_sessions() as session:
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
async def test_soft_delete_automatic_retry_cap_and_orphan_protection(client):
    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    store = MemoryStorage()
    store.list_objects = list
    await process_audio_job(job_id, session_factory=client.test_sessions, storage=store)
    async with client.test_sessions() as session:
        asset = await session.scalar(select(AudioAsset))
        asset.status = "deleting"
        store.fail_delete = True
        await session.commit()
    for _ in range(4):
        await storage_once(client.test_sessions, store)
    async with client.test_sessions() as session:
        asset = await session.get(AudioAsset, asset.id)
        assert asset.delete_attempts == 3 and asset.status == "delete_failed"
        asset.delete_attempts = 0
        store.fail_delete = False
        await session.commit()
    await storage_once(client.test_sessions, store)
    async with client.test_sessions() as session:
        assert (await session.get(AudioAsset, asset.id)).status == "deleted"


def test_typed_defaults_env_overrides_and_log_allowlist(monkeypatch):
    settings = Settings(_env_file=None)
    assert (
        settings.openrouter_model
        == settings.openrouter_evaluation_model
        == "z-ai/glm-5.3-flash"
    )


@pytest.mark.asyncio
async def test_upload_succeeded_but_asset_commit_failed_reuses_same_object(
    client, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession

    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    store = MemoryStorage()
    original = AsyncSession.flush
    once = True

    async def flush(session, *args, **kwargs):
        nonlocal once
        if once and any(isinstance(item, AudioAsset) for item in session.new):
            once = False
            raise OSError("Controlled database interruption after upload")
        return await original(session, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", flush)
    await process_audio_job(job_id, session_factory=client.test_sessions, storage=store)
    assert len(store.objects) == 1
    retried = await client.post(f"/jobs/{job_id}/retry", json={})
    assert (
        retried.status_code == 202 and retried.json()["retry_mode"] == "resume_upload"
    )
    await process_audio_job(
        uuid.UUID(retried.json()["job"]["id"]),
        session_factory=client.test_sessions,
        storage=store,
    )
    assert len(store.objects) == 1 and store.upload_attempts == 2
    async with client.test_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AudioAsset)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProviderCall)
                .where(ProviderCall.operation == "audio_generation")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_orphan_cleanup_preserves_referenced_and_active_objects(
    client, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    store = MemoryStorage()
    await process_audio_job(job_id, session_factory=client.test_sessions, storage=store)
    known_key = next(iter(store.objects))[1]
    old = datetime.now(UTC) - timedelta(days=2)
    store.objects[store.bucket, "audio/orphan.mp3"] = b"orphan"
    store.list_objects = lambda: [
        SimpleNamespace(object_name=key, last_modified=old) for _, key in store.objects
    ]
    fragment = tmp_path / "unreferenced.part"
    fragment.write_bytes(b"fragment")
    import os

    os.utime(fragment, (old.timestamp(), old.timestamp()))
    await storage_once(client.test_sessions, store)
    assert (store.bucket, known_key) in store.objects
    assert (
        store.bucket,
        "audio/orphan.mp3",
    ) not in store.objects and not fragment.exists()


@pytest.mark.asyncio
async def test_event_call_execution_pagination_and_empty_metrics(client):
    empty = (await client.get("/metrics/overview?all_time=true")).json()
    assert empty["total_jobs"] == 0 and empty["success_rate"] is None
    job_id = await failed_generation(client)
    for route in ("events", "calls", "executions"):
        response = await client.get(f"/jobs/{job_id}/{route}?limit=1&offset=0")
        assert response.status_code == 200
        page = response.json()
        assert page["total"] >= 1 and len(page["items"]) == 1
        assert (await client.get(f"/jobs/{job_id}/{route}?limit=0")).status_code == 422
    assert (
        await client.post(f"/jobs/{uuid.uuid4()}/retry", json={})
    ).status_code == 404


def test_log_and_env_allowlist(monkeypatch):
    monkeypatch.setenv("GENERATION_TOKENS_SHORT", "9000")
    assert Settings(_env_file=None).generation_tokens_short == 9000
    record = logging.LogRecord(
        "app.test", logging.ERROR, "", 0, "heartbeat_failed", (), None
    )
    record.job_id = "job"
    record.api_key = "secret"
    record.prompt = "private article"
    result = json.loads(SafeJsonFormatter().format(record))
    assert (
        result["job_id"] == "job"
        and "secret" not in json.dumps(result)
        and "prompt" not in result
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("evaluation_mode", [False, True])
async def test_truncated_responses_preserve_safe_model_usage_and_cost(
    client, evaluation_mode
):
    import httpx

    from app.evaluation_engine import OpenRouterEvaluationProvider
    from app.llm import OpenRouterProvider

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
        _, script = await approved_script(client)
        response = await client.post(f"/scripts/{script.id}/evaluate")
        job_id = uuid.UUID(response.json()["job"]["id"])
        await process_evaluation_job(
            job_id,
            session_factory=client.test_sessions,
            provider=OpenRouterEvaluationProvider(settings, transport),
        )
    else:
        _, job_id = await seed_job(client.test_sessions)
        await process_generation_job(
            job_id,
            session_factory=client.test_sessions,
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


@pytest.mark.asyncio
async def test_publish_success_commit_failure_allows_duplicate_without_duplicate_result(
    client, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession

    _, job_id = await seed_job(client.test_sessions)
    async with client.test_sessions() as session:
        outbox = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        outbox.status = "pending"
        await session.commit()
    original = AsyncSession.commit
    once = True

    async def commit(session):
        nonlocal once
        if once and any(
            isinstance(row, TaskOutbox) and row.status == "dispatched"
            for row in session.dirty
        ):
            once = False
            raise OSError("Controlled interruption after broker accepted the message")
        return await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    sent = []
    with pytest.raises(OSError):
        await dispatch_once(client.test_sessions, lambda *a, **kw: sent.append(kw))
    assert (
        await dispatch_once(client.test_sessions, lambda *a, **kw: sent.append(kw)) == 1
    )
    assert len(sent) == 2
    for _ in sent:
        await process_generation_job(
            job_id, session_factory=client.test_sessions, provider=SuccessfulProvider()
        )
    assert (await client.get(f"/jobs/{job_id}/calls")).json()["total"] == 1


@pytest.mark.asyncio
async def test_abandoned_duration_excluded_and_demo_refuses_paid_configuration(
    client, monkeypatch
):
    _, job_id = await seed_job(client.test_sessions)
    execution_id = await claim(client.test_sessions, job_id, "script_generation")
    await expire(client, execution_id)
    await recover_once(client.test_sessions)
    await process_generation_job(
        job_id, session_factory=client.test_sessions, provider=SuccessfulProvider()
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
