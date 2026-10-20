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
from app.evaluation_engine import (
    EvaluationProvider,
    get_evaluation_provider,
    run_rules,
)
from app.llm import (
    PROMPT_VERSION,
    LLMProvider,
    ProviderError,
    estimated_duration_seconds,
    get_provider,
)
from app.models import (
    Article,
    Evaluation,
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
    evaluation_id: uuid.UUID | None = None,
    rule_issues: list[dict] | None = None,
) -> None:
    async with session_factory() as session:
        job = await session.get(ProductionJob, job_id, with_for_update=True)
        call = await session.get(ProviderCall, call_id)
        evaluation = (
            await session.get(Evaluation, evaluation_id) if evaluation_id else None
        )
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
            if evaluation:
                evaluation.status = "queued"
                evaluation.issues = rule_issues or []
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
            if evaluation:
                evaluation.status = job.status
                evaluation.issues = rule_issues or []
                evaluation.completed_at = now
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
                operation="script_generation",
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
        await session.execute(
            select(Article).where(Article.id == job.article_id).with_for_update()
        )
        latest = await session.scalar(
            select(func.max(ScriptVersion.version)).where(
                ScriptVersion.article_id == job.article_id
            )
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
            estimated_duration_seconds=estimated_duration_seconds(
                result.text, job.config["target_language"]
            ),
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
                event_metadata={
                    "script_version_id": str(script.id),
                    "version": script.version,
                },
            )
        )
        await session.commit()


async def process_evaluation_job(
    job_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    provider: EvaluationProvider | None = None,
) -> None:
    async with session_factory() as session:
        job = await session.get(ProductionJob, job_id, with_for_update=True)
        if (
            job is None
            or job.job_type != "script_evaluation"
            or job.status != "queued"
            or job.result_evaluation_id is None
        ):
            return
        evaluation = await session.get(Evaluation, job.result_evaluation_id)
        script = await session.get(ScriptVersion, job.source_script_version_id)
        article = await session.get(Article, job.article_id)
        if evaluation is None or script is None or article is None:
            job.status = "failed"
            job.stage = "failed"
            job.error_code = "EVALUATION_INPUT_MISSING"
            job.error_message = "The article, script, or evaluation record no longer exists"
            job.completed_at = datetime.now(UTC)
            if evaluation:
                evaluation.status = "failed"
                evaluation.completed_at = job.completed_at
            session.add(
                JobEvent(
                    job_id=job.id,
                    event_type="failed",
                    stage="failed",
                    message=job.error_message,
                    event_metadata={"error_code": job.error_code},
                )
            )
            await session.commit()
            return
        now = datetime.now(UTC)
        job.status = "running"
        job.stage = "rule_check"
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.error_code = None
        job.error_message = None
        evaluation.status = "running"
        evaluation.started_at = evaluation.started_at or now
        session.add(
            JobEvent(
                job_id=job.id,
                event_type="started",
                stage="rule_check",
                message="Checking deterministic rules before the Judge call",
                event_metadata={"attempt": job.attempt_count},
            )
        )
        await session.commit()

    rule_issues = run_rules(article, script)
    try:
        active_provider = provider or get_evaluation_provider()
    except ProviderError as error:
        async with session_factory() as session:
            job = await session.get(ProductionJob, job_id)
            evaluation = await session.get(Evaluation, job.result_evaluation_id) if job else None
            if job:
                now = datetime.now(UTC)
                job.status = "failed"
                job.stage = "failed"
                job.error_code = error.code
                job.error_message = error.message
                job.completed_at = now
                if evaluation:
                    evaluation.status = "failed"
                    evaluation.issues = rule_issues
                    evaluation.completed_at = now
                session.add(
                    JobEvent(
                        job_id=job.id,
                        event_type="failed",
                        stage="failed",
                        message=error.message,
                        event_metadata={"error_code": error.code},
                    )
                )
                await session.commit()
        return

    call_id = uuid.uuid4()
    async with session_factory() as session:
        job = await session.get(ProductionJob, job_id)
        if job is None:
            return
        job.stage = "provider_call"
        session.add_all(
            [
                ProviderCall(
                    id=call_id,
                    job_id=job_id,
                    operation="script_evaluation",
                    provider=active_provider.name,
                    requested_model=active_provider.requested_model,
                    status="running",
                ),
                JobEvent(
                    job_id=job_id,
                    event_type="provider_call",
                    stage="provider_call",
                    message="Evaluating factual consistency, coverage, and audio readiness",
                    event_metadata={"rule_issue_count": len(rule_issues)},
                ),
            ]
        )
        await session.commit()

    try:
        result = await active_provider.evaluate_script(
            article, script, rule_issues, job_id
        )
    except ProviderError as error:
        await _record_failure(
            session_factory,
            job_id,
            call_id,
            error,
            evaluation_id=evaluation.id,
            rule_issues=rule_issues,
        )
        return
    except Exception as exc:  # noqa: BLE001 - persist unexpected provider errors
        await _record_failure(
            session_factory,
            job_id,
            call_id,
            ProviderError("PROVIDER_ERROR", str(exc) or "The provider call failed"),
            evaluation_id=evaluation.id,
            rule_issues=rule_issues,
        )
        return

    async with session_factory() as session:
        job = await session.get(ProductionJob, job_id, with_for_update=True)
        evaluation = await session.get(Evaluation, job.result_evaluation_id) if job else None
        call = await session.get(ProviderCall, call_id)
        if job is None or evaluation is None or call is None or job.status != "running":
            return
        now = datetime.now(UTC)
        evaluation.status = "succeeded"
        evaluation.factual_consistency = result.factual_consistency
        evaluation.content_coverage = result.content_coverage
        evaluation.audio_readiness = result.audio_readiness
        evaluation.issues = [*rule_issues, *result.issues]
        evaluation.provider = result.provider
        evaluation.requested_model = result.requested_model
        evaluation.actual_model = result.actual_model
        evaluation.completed_at = now
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
                message="The evaluation report is ready",
                event_metadata={
                    "evaluation_id": str(evaluation.id),
                    "issue_count": len(evaluation.issues),
                },
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


@celery_app.task(name="app.worker.evaluate_script_task")
def evaluate_script_task(job_id: str) -> None:
    async def run() -> None:
        worker_engine = create_async_engine(
            get_settings().database_url, pool_pre_ping=True
        )
        worker_sessions = async_sessionmaker(worker_engine, expire_on_commit=False)
        try:
            await process_evaluation_job(
                uuid.UUID(job_id), session_factory=worker_sessions
            )
        finally:
            await worker_engine.dispose()

    asyncio.run(run())
