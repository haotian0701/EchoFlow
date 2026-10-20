import pytest
from sqlalchemy import func, select

from app.models.jobs import ProductionJob, ProviderCall, TaskOutbox
from app.models.reviews import Evaluation
from app.services.script_production import process_evaluation_job
from tests.database import make_database
from tests.factories import seed_evaluation_job
from tests.fakes import FailingJudge, SuccessfulJudge


@pytest.mark.asyncio
async def test_evaluation_worker_succeeds_and_ignores_duplicate_delivery():
    engine, sessions = await make_database()
    job_id, evaluation_id = await seed_evaluation_job(sessions)
    await process_evaluation_job(
        job_id, session_factory=sessions, provider=SuccessfulJudge()
    )
    await process_evaluation_job(
        job_id, session_factory=sessions, provider=SuccessfulJudge()
    )

    async with sessions() as session:
        job = await session.get(ProductionJob, job_id)
        evaluation = await session.get(Evaluation, evaluation_id)
        assert job.status == "succeeded"
        assert float(evaluation.factual_consistency) == 0.9
        assert evaluation.provider == "test"
        assert await session.scalar(select(func.count()).select_from(ProviderCall)) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_evaluation_worker_retries_and_marks_unknown_result():
    engine, sessions = await make_database()
    retry_job_id, retry_evaluation_id = await seed_evaluation_job(sessions)
    await process_evaluation_job(
        retry_job_id,
        session_factory=sessions,
        provider=FailingJudge(retryable=True),
    )
    async with sessions() as session:
        job = await session.get(ProductionJob, retry_job_id)
        evaluation = await session.get(Evaluation, retry_evaluation_id)
        outbox = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == retry_job_id)
        )
        assert job.status == "retry_scheduled"
        assert evaluation.status == "queued"
        assert outbox.status == "pending"

    ambiguous_job_id, ambiguous_evaluation_id = await seed_evaluation_job(sessions)
    await process_evaluation_job(
        ambiguous_job_id,
        session_factory=sessions,
        provider=FailingJudge(ambiguous=True),
    )
    async with sessions() as session:
        job = await session.get(ProductionJob, ambiguous_job_id)
        evaluation = await session.get(Evaluation, ambiguous_evaluation_id)
        assert job.status == "needs_attention"
        assert evaluation.status == "needs_attention"
    await engine.dispose()
