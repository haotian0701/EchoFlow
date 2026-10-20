"""Database-owned leases and fenced state changes shared by all task types."""

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.core.provider_errors import ProviderError
from app.core.time import utc
from app.models.articles import Article
from app.models.jobs import (
    JobEvent,
    JobExecution,
    ProductionJob,
    ProviderCall,
    TaskOutbox,
)
from app.models.reviews import Evaluation

logger = logging.getLogger(__name__)


async def lock_job(session, job_id):
    article_id = await session.scalar(
        select(ProductionJob.article_id).where(ProductionJob.id == job_id)
    )
    if article_id is None:
        return None
    await session.execute(
        select(Article.id).where(Article.id == article_id).with_for_update()
    )
    return await session.scalar(
        select(ProductionJob)
        .where(ProductionJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def fenced(session, job_id, execution_id):
    job = await lock_job(session, job_id)
    if not job or job.status != "running" or job.current_execution_id != execution_id:
        return None
    execution = await session.get(JobExecution, execution_id, populate_existing=True)
    if (
        not execution
        or execution.status != "running"
        or utc(execution.lease_expires_at) <= datetime.now(UTC)
    ):
        return None
    return job


def event(session, job, kind, message, **metadata):
    logger.info(
        kind,
        extra={
            "job_id": str(job.id),
            "article_id": str(job.article_id),
            "execution_id": str(job.current_execution_id)
            if job.current_execution_id
            else None,
            "script_version_id": str(job.source_script_version_id)
            if job.source_script_version_id
            else None,
        },
    )
    session.add(
        JobEvent(
            job_id=job.id,
            event_type=kind,
            stage=job.stage,
            message=message,
            event_metadata=metadata,
        )
    )


async def claim(factory, job_id, kind):
    async with factory() as session:
        job = await lock_job(session, job_id)
        if not job or job.job_type != kind or job.status != "queued":
            return None
        if job.attempt_count >= job.max_attempts:
            job.status = job.stage = "failed"
            job.error_code, job.error_message = (
                "ATTEMPTS_EXHAUSTED",
                "Automatic execution limit reached",
            )
            job.completed_at = datetime.now(UTC)
            await session.commit()
            return None
        now = datetime.now(UTC)
        job.attempt_count += 1
        execution = JobExecution(
            id=uuid.uuid4(),
            job_id=job.id,
            attempt_no=job.attempt_count,
            stage="preparing",
            status="running",
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=get_settings().job_lease_seconds),
            started_at=now,
        )
        session.add(execution)
        await session.flush()
        job.current_execution_id = execution.id
        job.root_job_id = job.root_job_id or job.id
        job.status, job.stage = "running", "preparing"
        job.started_at = job.started_at or now
        job.completed_at = None
        job.error_code = job.error_message = None
        if kind == "script_evaluation":
            evaluation = await session.get(Evaluation, job.result_evaluation_id)
            if evaluation:
                evaluation.status = "running"
                evaluation.started_at = evaluation.started_at or now
        event(
            session,
            job,
            "started",
            "Execution claimed",
            execution_id=str(execution.id),
            attempt=job.attempt_count,
        )
        await session.commit()
        return execution.id


async def heartbeat(factory, job_id, execution_id):
    while True:
        try:
            await asyncio.wait_for(
                asyncio.Event().wait(), get_settings().job_heartbeat_seconds
            )
        except TimeoutError:
            pass
        try:
            async with factory() as session:
                job = await fenced(session, job_id, execution_id)
                if not job:
                    return
                execution = await session.get(JobExecution, execution_id)
                execution.heartbeat_at = datetime.now(UTC)
                execution.lease_expires_at = execution.heartbeat_at + timedelta(
                    seconds=get_settings().job_lease_seconds
                )
                execution.stage = job.stage
                await session.commit()
        except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
            logger.error(
                "heartbeat_failed",
                extra={"job_id": str(job_id), "execution_id": str(execution_id)},
            )


async def finish(session, job, status="succeeded"):
    now = datetime.now(UTC)
    job.status, job.stage, job.completed_at = (
        status,
        "completed" if status == "succeeded" else status,
        now,
    )
    execution = await session.get(JobExecution, job.current_execution_id)
    execution.status = "succeeded" if status == "succeeded" else "failed"
    execution.stage, execution.completed_at = job.stage, now


def apply_call_metadata(call, metadata):
    for field in (
        "actual_model",
        "provider_request_id",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "latency_ms",
        "provider_credits",
        "audio_duration_seconds",
    ):
        value = metadata.get(field)
        if value is not None:
            setattr(call, field, value)


async def record_failure(
    factory, job_id, execution_id, error, call_id=None, rule_issues=None
):
    async with factory() as session:
        job = await fenced(session, job_id, execution_id)
        if not job:
            return
        call = await session.get(ProviderCall, call_id) if call_id else None
        if call and call.status != "succeeded":
            call.status = "unknown" if error.ambiguous else "failed"
            call.retryable, call.uncertain = error.retryable, error.ambiguous
            call.error_code, call.error_message = error.code, error.message[:2000]
            call.completed_at = datetime.now(UTC)
            apply_call_metadata(call, getattr(error, "metadata", {}))
        evaluation = (
            await session.get(Evaluation, job.result_evaluation_id)
            if job.job_type == "script_evaluation"
            else None
        )
        job.error_code, job.error_message = error.code, error.message
        await finish(session, job, "needs_attention" if error.ambiguous else "failed")
        if (
            error.retryable
            and not error.ambiguous
            and job.attempt_count < job.max_attempts
        ):
            delay = 5 if job.attempt_count == 1 else 30
            job.status = job.stage = "retry_scheduled"
            job.completed_at = None
            outbox = await session.scalar(
                select(TaskOutbox).where(TaskOutbox.job_id == job.id).with_for_update()
            )
            if outbox:
                outbox.status, outbox.dispatched_at = "pending", None
                outbox.available_at = datetime.now(UTC) + timedelta(seconds=delay)
            event(
                session,
                job,
                "retry_scheduled",
                f"Temporary error; retrying in {delay} seconds",
                error_code=error.code,
            )
        else:
            event(session, job, job.status, error.message, error_code=error.code)
        if evaluation:
            evaluation.status = (
                "queued" if job.status == "retry_scheduled" else job.status
            )
            evaluation.issues = rule_issues or evaluation.issues or []
            evaluation.completed_at = job.completed_at
        await session.commit()


@asynccontextmanager
async def execution_scope(factory, job_id, kind):
    execution_id = await claim(factory, job_id, kind)
    if execution_id is None:
        yield None
        return
    task = asyncio.create_task(heartbeat(factory, job_id, execution_id))
    try:
        yield execution_id
    except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
        logger.error(
            "execution_interrupted",
            extra={"job_id": str(job_id), "execution_id": str(execution_id)},
        )
        async with factory() as session:
            call = await session.scalar(
                select(ProviderCall).where(ProviderCall.execution_id == execution_id)
            )
        await record_failure(
            factory,
            job_id,
            execution_id,
            ProviderError(
                "EXECUTION_INTERRUPTED",
                "Processing was interrupted. Review the provider result before retrying.",
                ambiguous=call is not None,
            ),
            call.id if call else None,
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
