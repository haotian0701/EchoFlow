import asyncio
import logging

from app.services.recovery import recover_once
from app.services.storage_cleanup import storage_once

logger = logging.getLogger(__name__)


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
    from app.core.logging import configure_logging

    configure_logging()
    asyncio.run(run())
