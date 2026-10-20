import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import ProductionJob, TaskOutbox


async def dispatch_once() -> int:
    dispatched = 0
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(TaskOutbox)
                .where(
                    TaskOutbox.status == "pending",
                    TaskOutbox.available_at <= datetime.now(UTC),
                )
                .order_by(TaskOutbox.available_at)
                .limit(20)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            try:
                await asyncio.to_thread(
                    celery_app.send_task,
                    row.task_name,
                    args=[str(row.job_id)],
                    task_id=str(row.job_id),
                    queue="script_generation",
                )
                row.status = "dispatched"
                row.dispatch_attempts += 1
                row.dispatched_at = datetime.now(UTC)
                row.last_error = None
                job = await session.get(ProductionJob, row.job_id)
                if job and job.status == "retry_scheduled":
                    job.status = "queued"
                    job.stage = "queued"
                dispatched += 1
            except Exception as exc:  # noqa: BLE001 - broker failures must remain in the outbox
                row.dispatch_attempts += 1
                row.last_error = str(exc)[:1000]
                row.available_at = datetime.now(UTC) + timedelta(seconds=5)
        await session.commit()
    return dispatched


async def run() -> None:
    while True:
        try:
            count = await dispatch_once()
        except Exception:  # noqa: BLE001 - keep the long-running dispatcher alive
            count = 0
        await asyncio.sleep(0.2 if count else 1.0)


if __name__ == "__main__":
    asyncio.run(run())
