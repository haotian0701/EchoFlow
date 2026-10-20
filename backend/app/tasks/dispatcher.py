import asyncio

from app.db.session import SessionLocal
from app.services.dispatch import dispatch_once, logger
from app.tasks.celery_app import celery_app


async def run():
    while True:
        try:
            count = await dispatch_once(SessionLocal, celery_app.send_task)
        except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
            logger.error("dispatcher_iteration_failed")
            count = 0
        await asyncio.sleep(0.2 if count else 1)


if __name__ == "__main__":
    from app.core.logging import configure_logging

    configure_logging()
    asyncio.run(run())
