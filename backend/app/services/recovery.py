import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.time import utc
from app.db.session import SessionLocal
from app.integrations.audio_staging import verify
from app.models.jobs import (
    JobExecution,
    MaintenanceHeartbeat,
    ProductionJob,
    ProviderCall,
    TaskOutbox,
)
from app.models.reviews import Evaluation
from app.services.execution import apply_call_metadata, event, lock_job

logger = logging.getLogger(__name__)


async def requeue(session, job, message):
    job.status = job.stage = "queued"
    job.completed_at = None
    row = await session.scalar(
        select(TaskOutbox).where(TaskOutbox.job_id == job.id).with_for_update()
    )
    if row:
        row.status, row.available_at, row.dispatched_at = (
            "pending",
            datetime.now(UTC),
            None,
        )
    if job.job_type == "script_evaluation":
        evaluation = await session.get(Evaluation, job.result_evaluation_id)
        if evaluation:
            evaluation.status, evaluation.completed_at = "queued", None
    event(session, job, "recovered", message)


async def recover_once(session_factory=SessionLocal):
    now = datetime.now(UTC)
    async with session_factory() as session:
        ids = (
            await session.scalars(
                select(ProductionJob.id).where(
                    ProductionJob.status.in_(["running", "queued", "needs_attention"])
                )
            )
        ).all()
    for job_id in ids:
        async with session_factory() as session:
            job = await lock_job(session, job_id)
            execution = (
                await session.get(JobExecution, job.current_execution_id)
                if job.current_execution_id
                else None
            )
            if (
                job.status == "running"
                and execution
                and utc(execution.lease_expires_at) > now
            ):
                continue
            if job.status == "queued":
                outbox = await session.scalar(
                    select(TaskOutbox)
                    .where(TaskOutbox.job_id == job.id)
                    .with_for_update()
                )
                if (
                    outbox
                    and outbox.status == "dispatched"
                    and outbox.dispatched_at
                    and utc(outbox.dispatched_at) < now - timedelta(seconds=120)
                ):
                    await requeue(
                        session,
                        job,
                        "Unclaimed delivery was lost; republishing the same job",
                    )
                    await session.commit()
                continue
            if job.status not in ("running", "needs_attention"):
                continue
            from app.services.artifacts import find_artifact

            artifact = (
                await find_artifact(session, job)
                if job.job_type == "audio_generation"
                else None
            )
            metadata = await asyncio.to_thread(verify, artifact) if artifact else None
            if artifact and artifact.status == "ready" and not metadata:
                artifact.status = "invalid"
                artifact.last_error = "Audio checkpoint is missing, expired, or damaged"
            if job.status == "needs_attention":
                child = await session.scalar(
                    select(ProductionJob.id).where(
                        ProductionJob.retry_of_job_id == job.id
                    )
                )
                if child or not metadata:
                    await session.commit()
                    continue
            calls = (
                await session.scalars(
                    select(ProviderCall).where(ProviderCall.job_id == job.id)
                )
            ).all()
            if execution and execution.status == "running":
                execution.status, execution.completed_at = "abandoned", now
            result_exists = bool(
                job.result_script_version_id or job.result_audio_asset_id
            )
            if job.job_type == "script_evaluation" and job.result_evaluation_id:
                evaluation = await session.get(Evaluation, job.result_evaluation_id)
                result_exists = (
                    evaluation is not None and evaluation.status == "succeeded"
                )
            if result_exists:
                job.status, job.stage, job.completed_at = "succeeded", "completed", now
                event(
                    session,
                    job,
                    "reconciled",
                    "Existing business result reconciled; no duplicate output created",
                )
            elif metadata and job.attempt_count < job.max_attempts:
                call = await session.get(ProviderCall, artifact.provider_call_id)
                apply_call_metadata(call, metadata)
                call.audio_duration_seconds = metadata["duration_seconds"]
                call.status, call.uncertain = "succeeded", False
                artifact.status, artifact.sha256 = "ready", metadata["sha256"]
                artifact.file_size, artifact.duration_seconds = (
                    metadata["file_size"],
                    metadata["duration_seconds"],
                )
                job.config = {**job.config, "resume_artifact_id": str(artifact.id)}
                await requeue(
                    session,
                    job,
                    "Verified audio checkpoint; resuming upload without synthesis",
                )
            elif not calls and job.attempt_count < job.max_attempts:
                await requeue(
                    session,
                    job,
                    "Lease expired before any provider call; safe to execute again",
                )
            else:
                for call in calls:
                    if call.status == "running":
                        call.status, call.uncertain = "unknown", True
                        call.error_code = "EXECUTION_LOST"
                job.status = job.stage = "needs_attention" if calls else "failed"
                job.error_code = "EXECUTION_LOST" if calls else "ATTEMPTS_EXHAUSTED"
                job.error_message = "Execution lease expired. Provider result may be unknown; review before retrying."
                job.completed_at = now
                if job.job_type == "script_evaluation":
                    evaluation = await session.get(Evaluation, job.result_evaluation_id)
                    if evaluation:
                        evaluation.status, evaluation.completed_at = job.status, now
                event(session, job, "lease_expired", job.error_message)
            await session.commit()
    async with session_factory() as session:
        heartbeat = await session.get(MaintenanceHeartbeat, "recovery")
        if heartbeat:
            heartbeat.heartbeat_at = datetime.now(UTC)
        else:
            session.add(
                MaintenanceHeartbeat(name="recovery", heartbeat_at=datetime.now(UTC))
            )
        await session.commit()
