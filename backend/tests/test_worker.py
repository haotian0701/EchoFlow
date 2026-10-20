import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.llm import GenerationResult, ProviderError
from app.models import Article, Base, ProductionJob, ScriptVersion, TaskOutbox
from app.worker import process_generation_job


class SuccessfulProvider:
    name = "test"
    requested_model = "test/model"

    async def generate_script(self, article, config, job_id):
        return GenerationResult(
            text=("Welcome to the report. The source states that revenue grew by 12 percent in Berlin. " * 3).strip(),
            provider=self.name,
            requested_model=self.requested_model,
            actual_model="test/model-v1",
            provider_request_id=f"test-{job_id}",
            total_tokens=42,
            latency_ms=5,
        )


class FailingProvider:
    name = "test"
    requested_model = "test/model"

    def __init__(self, *, retryable=False, ambiguous=False):
        self.retryable = retryable
        self.ambiguous = ambiguous

    async def generate_script(self, article, config, job_id):
        raise ProviderError(
            "TEST_PROVIDER_ERROR",
            "Provider failed",
            retryable=self.retryable,
            ambiguous=self.ambiguous,
        )


async def make_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def seed_job(sessions, *, article_id=None, source_script_id=None):
    async with sessions() as session:
        if article_id is None:
            article = Article(
                title="Source report",
                source="Newsroom",
                original_text=("Revenue grew by 12 percent in Berlin. " * 5).strip(),
                language="en",
                import_type="manual",
                content_hash=uuid.uuid4().hex.ljust(64, "0"),
            )
            session.add(article)
            await session.flush()
            article_id = article.id
        job = ProductionJob(
            job_type="script_generation",
            article_id=article_id,
            source_script_version_id=source_script_id,
            status="queued",
            stage="queued",
            config={"target_language": "en", "length": "short", "style": "news", "audience": "general"},
            input_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
            attempt_count=0,
            max_attempts=3,
        )
        session.add(job)
        await session.flush()
        session.add(TaskOutbox(job_id=job.id, task_name="test", status="dispatched"))
        await session.commit()
        return article_id, job.id


@pytest.mark.asyncio
async def test_worker_creates_versions_and_ignores_duplicate_delivery():
    engine, sessions = await make_database()
    article_id, first_job_id = await seed_job(sessions)
    await process_generation_job(first_job_id, session_factory=sessions, provider=SuccessfulProvider())
    await process_generation_job(first_job_id, session_factory=sessions, provider=SuccessfulProvider())

    async with sessions() as session:
        first_job = await session.get(ProductionJob, first_job_id)
        first_script = await session.get(ScriptVersion, first_job.result_script_version_id)
        assert first_job.status == "succeeded"
        assert first_script.version == 1
        assert await session.scalar(select(func.count()).select_from(ScriptVersion)) == 1

    _, second_job_id = await seed_job(
        sessions, article_id=article_id, source_script_id=first_script.id
    )
    await process_generation_job(second_job_id, session_factory=sessions, provider=SuccessfulProvider())
    async with sessions() as session:
        second_job = await session.get(ProductionJob, second_job_id)
        second_script = await session.get(ScriptVersion, second_job.result_script_version_id)
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
        outbox = await session.scalar(select(TaskOutbox).where(TaskOutbox.job_id == retry_job_id))
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
