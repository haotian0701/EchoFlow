import pytest
from sqlalchemy import func, select

from app.integrations.tts import MockTTSProvider
from app.models.audio import AudioAsset
from app.models.jobs import ProductionJob, ProviderCall, TaskOutbox
from app.services.audio_production import process_audio_job
from tests.factories import approved_script, queued_job
from tests.fakes import FailingTTS, MemoryStorage

BODY = {"language": "en", "voice_id": "mock-chime", "speed": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["evaluation", "version"])
async def test_worker_rechecks_approval_before_paid_call(sessions, client, change):
    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    if change == "evaluation":
        await client.post(f"/scripts/{script.id}/evaluate")
    else:
        await client.patch(
            f"/scripts/{script.id}",
            json={"text": script.text + " Updated.", "expected_version": 1},
        )
    await process_audio_job(job_id, session_factory=sessions, storage=MemoryStorage())
    async with sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == "failed" and job.error_code == "APPROVAL_STALE"
        assert await session.scalar(select(func.count()).select_from(ProviderCall)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retryable,ambiguous,expected",
    [
        (True, False, "retry_scheduled"),
        (False, True, "needs_attention"),
        (False, False, "failed"),
    ],
)
async def test_worker_failure_policy(sessions, client, retryable, ambiguous, expected):
    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    await process_audio_job(
        job_id,
        session_factory=sessions,
        provider=FailingTTS(retryable, ambiguous),
        storage=MemoryStorage(),
    )
    async with sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == expected and job.attempt_count == 1
        outbox = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        assert outbox.queue_name == "audio_generation"


@pytest.mark.asyncio
async def test_upload_failure_does_not_repeat_or_erase_successful_provider_call(
    sessions, client, monkeypatch
):
    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    storage = MemoryStorage()
    storage.fail_upload = True

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr("app.services.audio_production.asyncio.sleep", no_sleep)
    await process_audio_job(job_id, session_factory=sessions, storage=storage)
    assert storage.upload_attempts == 3
    async with sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == "needs_attention"
        call = await session.scalar(select(ProviderCall))
        assert call.status == "succeeded"
        assert await session.scalar(select(func.count()).select_from(AudioAsset)) == 0


@pytest.mark.asyncio
async def test_invalid_provider_audio_fails_without_creating_asset(sessions, client):
    from app.integrations.tts import SynthesisResult

    class InvalidAudio(MockTTSProvider):
        async def synthesize(self, text, voice, language, speed, job_id, output_path):
            output_path.write_bytes(b"not an MP3")
            return SynthesisResult()

    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    await process_audio_job(
        job_id,
        session_factory=sessions,
        provider=InvalidAudio(),
        storage=MemoryStorage(),
    )
    async with sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == "failed" and job.error_code == "INVALID_TTS_AUDIO"
        assert await session.scalar(select(func.count()).select_from(AudioAsset)) == 0


@pytest.mark.asyncio
async def test_retry_exhaustion_and_new_request_after_failure(sessions, client):
    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    for attempt in range(3):
        await process_audio_job(
            job_id,
            session_factory=sessions,
            provider=FailingTTS(retryable=True),
            storage=MemoryStorage(),
        )
        async with sessions() as session:
            job = await session.get(ProductionJob, job_id)
            assert job.attempt_count == attempt + 1
            if attempt < 2:
                assert job.status == "retry_scheduled"
                job.status = "queued"
                await session.commit()
            else:
                assert job.status == "failed"
    new_job = await queued_job(client, script)
    assert new_job != job_id
