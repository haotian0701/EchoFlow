import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.celery_app import celery_app
from app.config import get_settings
from app.database import SessionLocal
from app.llm import (
    PROMPT_VERSION,
    LLMProvider,
    ProviderError,
    estimated_duration_seconds,
    get_provider,
)
from app.models import (
    Article,
    JobEvent,
    ProductionJob,
    ProviderCall,
    ScriptVersion,
    TaskOutbox,
)
from app.schemas import ScriptGenerationConfig


async def _record_failure(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    call_id: uuid.UUID,
    error: ProviderError,
) -> None:
    async with session_factory() as session:
        job = await session.get(ProductionJob, job_id, with_for_update=True)
        call = await session.get(ProviderCall, call_id)
        if job is None or call is None:
            return
        now = datetime.now(UTC)
        call.status = "failed"
        call.error_code = error.code
        call.error_message = error.message[:2000]
        call.completed_at = now
        if error.retryable and job.attempt_count < job.max_attempts:
            delay = 5 if job.attempt_count == 1 else 30
            job.status = "retry_scheduled"
            job.stage = "retry_scheduled"
            job.error_code = error.code
            job.error_message = error.message
            outbox = await session.scalar(select(TaskOutbox).where(TaskOutbox.job_id == job.id))
            if outbox:
                outbox.status = "pending"
                outbox.available_at = now + timedelta(seconds=delay)
                outbox.dispatched_at = None
            session.add(
                JobEvent(
                    job_id=job.id,
                    event_type="retry_scheduled",
                    stage="retry_scheduled",
                    message=f"Temporary provider error; retrying in {delay} seconds",
                    event_metadata={"error_code": error.code, "attempt": job.attempt_count},
                )
            )
        else:
            job.status = "needs_attention" if error.ambiguous else "failed"
            job.stage = job.status
            job.error_code = error.code
            job.error_message = error.message
            job.completed_at = now
            session.add(
                JobEvent(
                    job_id=job.id,
                    event_type="attention_required" if error.ambiguous else "failed",
                    stage=job.status,
                    message=error.message,
                    event_metadata={"error_code": error.code},
                )
            )
        await session.commit()


async def process_generation_job(
    job_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    provider: LLMProvider | None = None,
) -> None:
    async with session_factory() as session:
        job = await session.get(ProductionJob, job_id, with_for_update=True)
        if job is None or job.status != "queued" or job.result_script_version_id is not None:
            return
        article = await session.get(Article, job.article_id)
        if article is None:
            job.status = "failed"
            job.stage = "failed"
            job.error_code = "ARTICLE_NOT_FOUND"
            job.error_message = "The source article no longer exists"
            job.completed_at = datetime.now(UTC)
            await session.commit()
            return
        job.status = "running"
        job.stage = "provider_call"
        job.attempt_count += 1
        job.started_at = job.started_at or datetime.now(UTC)
        job.error_code = None
        job.error_message = None
        session.add(
            JobEvent(
                job_id=job.id,
                event_type="started",
                stage="provider_call",
                message="Generating the audio script",
                event_metadata={"attempt": job.attempt_count},
            )
        )
        await session.commit()

    try:
        active_provider = provider or get_provider()
    except ProviderError as error:
        async with session_factory() as session:
            job = await session.get(ProductionJob, job_id)
            if job:
                job.status = "failed"
                job.stage = "failed"
                job.error_code = error.code
                job.error_message = error.message
                job.completed_at = datetime.now(UTC)
                session.add(JobEvent(job_id=job.id, event_type="failed", stage="failed", message=error.message, event_metadata={"error_code": error.code}))
                await session.commit()
        return

    call_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            ProviderCall(
                id=call_id,
                job_id=job_id,
                provider=active_provider.name,
                requested_model=active_provider.requested_model,
                status="running",
            )
        )
        await session.commit()

    try:
        result = await active_provider.generate_script(
            article, ScriptGenerationConfig.model_validate(job.config), job_id
        )
    except ProviderError as error:
        await _record_failure(session_factory, job_id, call_id, error)
        return
    except Exception as exc:  # noqa: BLE001 - convert unexpected provider failures into job state
        await _record_failure(
            session_factory,
            job_id,
            call_id,
            ProviderError("PROVIDER_ERROR", str(exc) or "The provider call failed"),
        )
        return

    async with session_factory() as session:
        job = await session.get(ProductionJob, job_id, with_for_update=True)
        call = await session.get(ProviderCall, call_id)
        if job is None or call is None or job.result_script_version_id is not None:
            return
        await session.execute(select(Article).where(Article.id == job.article_id).with_for_update())
        latest = await session.scalar(
            select(func.max(ScriptVersion.version)).where(ScriptVersion.article_id == job.article_id)
        )
        script = ScriptVersion(
            article_id=job.article_id,
            version=(latest or 0) + 1,
            text=result.text,
            source="generated",
            parent_version_id=job.source_script_version_id,
            target_language=job.config["target_language"],
            generation_config=job.config,
            provider=result.provider,
            requested_model=result.requested_model,
            actual_model=result.actual_model,
            prompt_version=PROMPT_VERSION,
            estimated_duration_seconds=estimated_duration_seconds(result.text, job.config["target_language"]),
        )
        session.add(script)
        await session.flush()
        now = datetime.now(UTC)
        job.result_script_version_id = script.id
        job.status = "succeeded"
        job.stage = "completed"
        job.completed_at = now
        call.status = "succeeded"
        call.actual_model = result.actual_model
        call.provider_request_id = result.provider_request_id
        call.prompt_tokens = result.prompt_tokens
        call.completion_tokens = result.completion_tokens
        call.total_tokens = result.total_tokens
        call.cost = result.cost
        call.latency_ms = result.latency_ms
        call.completed_at = now
        session.add(
            JobEvent(
                job_id=job.id,
                event_type="succeeded",
                stage="completed",
                message=f"Script version {script.version} is ready",
                event_metadata={"script_version_id": str(script.id), "version": script.version},
            )
        )
        await session.commit()


@celery_app.task(name="app.worker.generate_script_task")
def generate_script_task(job_id: str) -> None:
    async def run() -> None:
        worker_engine = create_async_engine(
            get_settings().database_url, pool_pre_ping=True
        )
        worker_sessions = async_sessionmaker(worker_engine, expire_on_commit=False)
        try:
            await process_generation_job(
                uuid.UUID(job_id), session_factory=worker_sessions
            )
        finally:
            await worker_engine.dispose()

    asyncio.run(run())
