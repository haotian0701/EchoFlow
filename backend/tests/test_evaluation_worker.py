import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.evaluation_engine import EvaluationResult
from app.llm import ProviderError
from app.models import (
    Article,
    Base,
    Evaluation,
    ProductionJob,
    ProviderCall,
    ScriptVersion,
    TaskOutbox,
)
from app.worker import process_evaluation_job


class SuccessfulJudge:
    name = "test"
    requested_model = "test/judge"

    async def evaluate_script(self, article, script, rule_issues, job_id):
        return EvaluationResult(
            factual_consistency=0.9,
            content_coverage=0.8,
            audio_readiness=0.7,
            issues=[],
            provider=self.name,
            requested_model=self.requested_model,
            actual_model="test/judge-v2",
            provider_request_id=f"judge-{job_id}",
            total_tokens=55,
            latency_ms=4,
        )


class FailingJudge:
    name = "test"
    requested_model = "test/judge"

    def __init__(self, *, retryable=False, ambiguous=False):
        self.retryable = retryable
        self.ambiguous = ambiguous

    async def evaluate_script(self, article, script, rule_issues, job_id):
        raise ProviderError(
            "JUDGE_FAILED",
            "Judge failed",
            retryable=self.retryable,
            ambiguous=self.ambiguous,
        )


async def make_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def seed_evaluation_job(sessions):
    async with sessions() as session:
        article = Article(
            title="Report",
            source="Newsroom",
            original_text=("Revenue rose by 12 percent in Berlin. " * 15).strip(),
            language="en",
            import_type="manual",
            content_hash=uuid.uuid4().hex.ljust(64, "0"),
        )
        session.add(article)
        await session.flush()
        script = ScriptVersion(
            article_id=article.id,
            version=1,
            text=("Revenue rose by 12 percent in Berlin. " * 10).strip(),
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
        session.add(script)
        await session.flush()
        job = ProductionJob(
            job_type="script_evaluation",
            article_id=article.id,
            source_script_version_id=script.id,
            status="queued",
            stage="queued",
            config={
                "rule_version": "evaluation-rules-v1",
                "prompt_version": "script-evaluation-v1",
            },
            input_fingerprint="c" * 64,
            attempt_count=0,
            max_attempts=3,
        )
        session.add(job)
        await session.flush()
        evaluation = Evaluation(
            script_version_id=script.id,
            job_id=job.id,
            status="queued",
            issues=[],
            rule_version="evaluation-rules-v1",
            prompt_version="script-evaluation-v1",
        )
        session.add(evaluation)
        await session.flush()
        job.result_evaluation_id = evaluation.id
        session.add(
            TaskOutbox(
                job_id=job.id,
                task_name="test",
                queue_name="script_evaluation",
                status="dispatched",
            )
        )
        await session.commit()
        return job.id, evaluation.id


@pytest.mark.asyncio
async def test_evaluation_worker_succeeds_and_ignores_duplicate_delivery():
    engine, sessions = await make_database()
    job_id, evaluation_id = await seed_evaluation_job(sessions)
    await process_evaluation_job(job_id, session_factory=sessions, provider=SuccessfulJudge())
    await process_evaluation_job(job_id, session_factory=sessions, provider=SuccessfulJudge())

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
