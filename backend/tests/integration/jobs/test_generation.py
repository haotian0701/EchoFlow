import pytest
from sqlalchemy import func, select

from app.models.jobs import ProductionJob, TaskOutbox
from app.models.scripts import ScriptVersion
from app.services.script_production import process_generation_job
from tests.database import make_database
from tests.factories import seed_job
from tests.fakes import FailingProvider, SuccessfulProvider


@pytest.mark.asyncio
async def test_worker_creates_versions_and_ignores_duplicate_delivery():
    engine, sessions = await make_database()
    article_id, first_job_id = await seed_job(sessions)
    await process_generation_job(
        first_job_id, session_factory=sessions, provider=SuccessfulProvider()
    )
    await process_generation_job(
        first_job_id, session_factory=sessions, provider=SuccessfulProvider()
    )

    async with sessions() as session:
        first_job = await session.get(ProductionJob, first_job_id)
        first_script = await session.get(
            ScriptVersion, first_job.result_script_version_id
        )
        assert first_job.status == "succeeded"
        assert first_script.version == 1
        assert (
            await session.scalar(select(func.count()).select_from(ScriptVersion)) == 1
        )

    _, second_job_id = await seed_job(
        sessions, article_id=article_id, source_script_id=first_script.id
    )
    await process_generation_job(
        second_job_id, session_factory=sessions, provider=SuccessfulProvider()
    )
    async with sessions() as session:
        second_job = await session.get(ProductionJob, second_job_id)
        second_script = await session.get(
            ScriptVersion, second_job.result_script_version_id
        )
        assert second_script.version == 2
        assert second_script.parent_version_id == first_script.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_schedules_retry_and_marks_ambiguous_result():
    engine, sessions = await make_database()
    article_id, retry_job_id = await seed_job(sessions)
    await process_generation_job(
        retry_job_id, session_factory=sessions, provider=FailingProvider(retryable=True)
    )
    async with sessions() as session:
        retry_job = await session.get(ProductionJob, retry_job_id)
        outbox = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == retry_job_id)
        )
        assert retry_job.status == "retry_scheduled"
        assert outbox.status == "pending"

    _, attention_job_id = await seed_job(sessions, article_id=article_id)
    await process_generation_job(
        attention_job_id,
        session_factory=sessions,
        provider=FailingProvider(ambiguous=True),
    )
    async with sessions() as session:
        attention_job = await session.get(ProductionJob, attention_job_id)
        assert attention_job.status == "needs_attention"
        assert attention_job.error_code == "TEST_PROVIDER_ERROR"
    await engine.dispose()
