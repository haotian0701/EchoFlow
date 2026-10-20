from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.metrics import get_overview

router = APIRouter(tags=["monitoring"])


@router.get("/metrics/overview")
async def overview(
    session: Annotated[AsyncSession, Depends(get_session)],
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    all_time: bool = False,
):
    return await get_overview(session, start, end, all_time)
