import asyncio
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings


def run_task(job_id, processor):
    async def run():
        engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        try:
            await processor(
                uuid.UUID(job_id),
                session_factory=async_sessionmaker(engine, expire_on_commit=False),
            )
        finally:
            await engine.dispose()

    asyncio.run(run())
