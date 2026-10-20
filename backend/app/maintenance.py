"""Recovery never relies on a live broker. Storage runs in an independent loop."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.audio_staging import remove, verify
from app.config import get_settings
from app.database import SessionLocal
from app.execution import apply_call_metadata, event, lock_job, utc
from app.models import (
    AudioArtifact,
    AudioAsset,
    Evaluation,
    JobExecution,
    MaintenanceHeartbeat,
    ProductionJob,
    ProviderCall,
    TaskOutbox,
)
from app.storage import get_audio_storage

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
            from app.audio_worker import find_artifact

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


async def storage_once(session_factory=SessionLocal, storage=None):
    now = datetime.now(UTC)
    store = storage or get_audio_storage()
    async with session_factory() as session:
        ids = (
            await session.scalars(
                select(AudioAsset.id).where(
                    AudioAsset.status.in_(["deleting", "delete_failed"]),
                    AudioAsset.delete_attempts < 3,
                )
            )
        ).all()
    for asset_id in ids:
        async with session_factory() as session:
            asset = await session.get(AudioAsset, asset_id, with_for_update=True)
            if (
                not asset
                or asset.status not in ("deleting", "delete_failed")
                or asset.delete_attempts >= 3
            ):
                continue
            asset.delete_attempts += 1
            try:
                await asyncio.to_thread(store.delete, asset.bucket, asset.object_key)
                asset.status, asset.deleted_at, asset.delete_error = (
                    "deleted",
                    now,
                    None,
                )
            except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
                asset.status, asset.delete_error = (
                    "delete_failed",
                    "Private storage deletion failed; retry from Audio Studio",
                )
            await session.commit()
    async with session_factory() as session:
        artifacts = (await session.scalars(select(AudioArtifact))).all()
        for artifact in artifacts:
            job = await lock_job(session, artifact.job_id)
            # Locking the article/job also excludes new resume jobs created by the retry API.
            active = await session.scalar(
                select(ProductionJob.id).where(
                    ProductionJob.article_id == job.article_id,
                    ProductionJob.status.in_(["queued", "running", "retry_scheduled"]),
                )
            )
            if active:
                continue
            if (
                artifact.status == "preparing"
                and utc(artifact.created_at) < now - timedelta(hours=24)
                and not await asyncio.to_thread(verify, artifact)
            ):
                await asyncio.to_thread(remove, artifact)
                artifact.status = "expired"
            if artifact.status == "completed" or utc(artifact.expires_at) <= now:
                await asyncio.to_thread(remove, artifact)
                if artifact.status != "completed":
                    artifact.status = "expired"
        await session.commit()
    # Only remove unreferenced fragments; complete referenced checkpoints retain their full 7 days.
    root = Path(get_settings().audio_staging_path)
    async with session_factory() as session:
        referenced = {
            str(a.id) for a in (await session.scalars(select(AudioArtifact))).all()
        }
    if root.exists():
        for path in root.iterdir():
            if (
                path.is_file()
                and path.name.split(".")[0] not in referenced
                and path.stat().st_mtime < (now - timedelta(hours=24)).timestamp()
            ):
                path.unlink(missing_ok=True)
    # An object reserved by any live artifact or asset is never an orphan.
    for obj in await asyncio.to_thread(lambda: list(store.list_objects())):
        if not obj.last_modified or utc(obj.last_modified) >= now - timedelta(hours=24):
            continue
        async with session_factory() as session:
            asset = await session.scalar(
                select(AudioAsset.id).where(
                    AudioAsset.object_key == obj.object_name,
                    AudioAsset.status != "deleted",
                )
            )
            artifact = await session.scalar(
                select(AudioArtifact).where(AudioArtifact.object_key == obj.object_name)
            )
            active = False
            if artifact:
                source_job = await lock_job(session, artifact.job_id)
                active = await session.scalar(
                    select(ProductionJob.id).where(
                        ProductionJob.article_id == source_job.article_id,
                        ProductionJob.status.in_(
                            ["queued", "running", "retry_scheduled"]
                        ),
                    )
                )
            protected = artifact and artifact.status not in ("expired", "completed")
            if not asset and not protected and not active:
                await asyncio.to_thread(store.delete, store.bucket, obj.object_name)


async def loop(action, seconds):
    while True:
        try:
            await action()
        except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
            logger.error(
                "maintenance_iteration_failed", extra={"operation": action.__name__}
            )
        await asyncio.sleep(seconds)


async def run():
    await asyncio.gather(loop(recover_once, 15), loop(storage_once, 60))


if __name__ == "__main__":
    from app.logging_config import configure_logging

    configure_logging()
    asyncio.run(run())
