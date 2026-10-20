import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.execution import utc
from app.models import MaintenanceHeartbeat
from app.storage import get_audio_storage

router = APIRouter()


@router.get("/health/ready")
async def ready(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
):
    checks = {}
    try:
        async with asyncio.timeout(5):
            await session.execute(text("SELECT 1"))
            checks["database"] = True
            beat = await session.get(MaintenanceHeartbeat, "recovery")
            checks["maintenance"] = bool(
                beat
                and utc(beat.heartbeat_at) > datetime.now(UTC) - timedelta(seconds=60)
            )
    except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
        checks.update(database=False, maintenance=False)

    async def redis_check():
        client = Redis.from_url(
            get_settings().celery_broker_url, socket_connect_timeout=3, socket_timeout=3
        )
        try:
            return bool(await client.ping())
        finally:
            await client.aclose()

    async def minio_check():
        return await asyncio.to_thread(get_audio_storage().ready)

    async def check(action):
        try:
            async with asyncio.timeout(5):
                return await action()
        except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
            return False

    checks["redis"], checks["minio"] = await asyncio.gather(
        check(redis_check), check(minio_check)
    )
    ok = all(checks.values())
    body = {"status": "ready" if ok else "not_ready", "checks": checks}
    if not ok:
        body.update(
            code="DEPENDENCIES_UNAVAILABLE",
            message="One or more local dependencies are unavailable",
            details={"checks": checks},
            request_id=getattr(request.state, "request_id", None),
        )
    return JSONResponse(body, status_code=200 if ok else 503)
