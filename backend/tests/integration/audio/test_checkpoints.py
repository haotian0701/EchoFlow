import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.integrations.audio_staging import paths, verify
from app.models.audio import AudioArtifact, AudioAsset
from app.models.jobs import JobExecution, ProductionJob, ProviderCall
from app.services.audio_production import process_audio_job
from app.services.recovery import recover_once
from tests.factories import approved_script, queued_job
from tests.fakes import MemoryStorage


@pytest.mark.asyncio
async def test_audio_upload_retry_reuses_checkpoint_call_and_historical_approval(
    sessions, client, monkeypatch
):
    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    storage = MemoryStorage()
    storage.fail_upload = True

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.services.audio_production.asyncio.sleep", no_sleep)
    await process_audio_job(job_id, session_factory=sessions, storage=storage)
    async with sessions() as session:
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
    await recover_once(sessions)
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == "needs_attention"
    storage.fail_upload = False
    await process_audio_job(child_id, session_factory=sessions, storage=storage)
    async with sessions() as session:
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
async def test_checkpoint_recovery_when_db_ready_update_was_lost(
    sessions, client, monkeypatch
):
    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    store = MemoryStorage()
    store.fail_upload = True

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.services.audio_production.asyncio.sleep", no_sleep)
    await process_audio_job(job_id, session_factory=sessions, storage=store)
    async with sessions() as session:
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
    await recover_once(sessions)
    store.fail_upload = False
    await process_audio_job(job_id, session_factory=sessions, storage=store)
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_checkpoint_tampering_and_expiration_require_charge_ack(
    sessions, client, monkeypatch
):
    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    store = MemoryStorage()
    store.fail_upload = True

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.services.audio_production.asyncio.sleep", no_sleep)
    await process_audio_job(job_id, session_factory=sessions, storage=store)
    async with sessions() as session:
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
async def test_upload_succeeded_but_asset_commit_failed_reuses_same_object(
    sessions, client, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession

    _, script = await approved_script(client, sessions)
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
    await process_audio_job(job_id, session_factory=sessions, storage=store)
    assert len(store.objects) == 1
    retried = await client.post(f"/jobs/{job_id}/retry", json={})
    assert (
        retried.status_code == 202 and retried.json()["retry_mode"] == "resume_upload"
    )
    await process_audio_job(
        uuid.UUID(retried.json()["job"]["id"]),
        session_factory=sessions,
        storage=store,
    )
    assert len(store.objects) == 1 and store.upload_attempts == 2
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AudioAsset)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProviderCall)
                .where(ProviderCall.operation == "audio_generation")
            )
            == 1
        )
