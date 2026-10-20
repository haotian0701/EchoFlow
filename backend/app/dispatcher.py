import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.celery_app import celery_app
from app.database import SessionLocal
from app.execution import lock_job, utc
from app.models import TaskOutbox

logger = logging.getLogger(__name__)


async def dispatch_once(session_factory=SessionLocal, sender=None):
    sender = sender or celery_app.send_task
    async with session_factory() as session:
        ids = (
            await session.scalars(
                select(TaskOutbox.job_id)
                .where(
                    TaskOutbox.status == "pending",
                    TaskOutbox.available_at <= datetime.now(UTC),
                )
                .order_by(TaskOutbox.available_at)
                .limit(20)
            )
        ).all()
    dispatched = 0
    for job_id in ids:
        # Commit runnable state before publishing so fast consumers can claim it.
        async with session_factory() as session:
            job = await lock_job(session, job_id)
            row = await session.scalar(
                select(TaskOutbox)
                .where(TaskOutbox.job_id == job_id)
                .with_for_update(skip_locked=True)
            )
            if (
                not row
                or row.status != "pending"
                or utc(row.available_at) > datetime.now(UTC)
            ):
                continue
            if job.status == "retry_scheduled":
                job.status = job.stage = "queued"
            await session.commit()
        async with session_factory() as session:
            job = await lock_job(session, job_id)
            row = await session.scalar(
                select(TaskOutbox)
                .where(TaskOutbox.job_id == job_id)
                .with_for_update(skip_locked=True)
            )
            if not row or row.status != "pending" or job.status != "queued":
                continue
            row.dispatch_attempts += 1
            try:
                await asyncio.to_thread(
                    sender,
                    row.task_name,
                    args=[str(job_id)],
                    task_id=str(job_id),
                    queue=row.queue_name,
                )
                row.status, row.dispatched_at, row.last_error = (
                    "dispatched",
                    datetime.now(UTC),
                    None,
                )
                dispatched += 1
            except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
                row.last_error = "Broker publish failed"
                row.available_at = datetime.now(UTC) + timedelta(seconds=5)
                logger.error("outbox_publish_failed", extra={"job_id": str(job_id)})
            await session.commit()
    return dispatched


async def run():
    while True:
        try:
            count = await dispatch_once()
        except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
            logger.error("dispatcher_iteration_failed")
            count = 0
        await asyncio.sleep(0.2 if count else 1)


if __name__ == "__main__":
    from app.logging_config import configure_logging

    configure_logging()
    asyncio.run(run())
