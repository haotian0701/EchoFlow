from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.errors import ApiError
from app.job_service import create_generation_job
from app.models import JobEvent, ProductionJob, ScriptVersion
from app.schemas import (
    JobCreate,
    JobCreateResult,
    JobEventList,
    JobList,
    JobOut,
    RegenerateRequest,
    ScriptDetail,
    ScriptGenerationConfig,
    ScriptList,
)

router = APIRouter(tags=["script generation"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("/jobs", response_model=JobCreateResult)
async def create_job(
    body: JobCreate,
    response: Response,
    session: SessionDep,
    idempotency_key: IdempotencyKey = None,
):
    job, replay = await create_generation_job(
        session,
        article_id=body.article_id,
        config=body.config,
        idempotency_key=idempotency_key,
    )
    response.status_code = 200 if replay else 202
    response.headers["Location"] = f"/jobs/{job.id}"
    return JobCreateResult(job=JobOut.model_validate(job), idempotent_replay=replay)


@router.get("/jobs", response_model=JobList)
async def list_jobs(
    session: SessionDep,
    article_id: UUID | None = None,
    job_type: Annotated[
        Literal["script_generation", "script_evaluation"] | None,
        Query(alias="type"),
    ] = None,
    status: Literal["queued", "running", "retry_scheduled", "succeeded", "failed", "needs_attention"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    filters = []
    if article_id:
        filters.append(ProductionJob.article_id == article_id)
    if job_type:
        filters.append(ProductionJob.job_type == job_type)
    if status:
        filters.append(ProductionJob.status == status)
    total = await session.scalar(select(func.count()).select_from(ProductionJob).where(*filters))
    items = (
        await session.scalars(
            select(ProductionJob)
            .where(*filters)
            .order_by(ProductionJob.created_at.desc(), ProductionJob.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return JobList(items=items, total=total or 0, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: UUID, session: SessionDep):
    job = await session.get(ProductionJob, job_id)
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", "Job not found")
    return job


@router.get("/jobs/{job_id}/events", response_model=JobEventList)
async def get_job_events(job_id: UUID, session: SessionDep):
    if await session.get(ProductionJob, job_id) is None:
        raise ApiError(404, "JOB_NOT_FOUND", "Job not found")
    items = (
        await session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id)
            .order_by(JobEvent.created_at, JobEvent.id)
        )
    ).all()
    return JobEventList(items=items)


@router.get("/articles/{article_id}/scripts", response_model=ScriptList)
async def list_scripts(
    article_id: UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    total = await session.scalar(
        select(func.count()).select_from(ScriptVersion).where(ScriptVersion.article_id == article_id)
    )
    items = (
        await session.scalars(
            select(ScriptVersion)
            .where(ScriptVersion.article_id == article_id)
            .order_by(ScriptVersion.version.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return ScriptList(items=items, total=total or 0, limit=limit, offset=offset)


@router.get("/scripts/{script_id}", response_model=ScriptDetail)
async def get_script(script_id: UUID, session: SessionDep):
    script = await session.get(ScriptVersion, script_id)
    if script is None:
        raise ApiError(404, "SCRIPT_NOT_FOUND", "Script version not found")
    return script


@router.post("/scripts/{script_id}/regenerate", response_model=JobCreateResult)
async def regenerate_script(
    script_id: UUID,
    body: RegenerateRequest | None,
    response: Response,
    session: SessionDep,
    idempotency_key: IdempotencyKey = None,
):
    script = await session.get(ScriptVersion, script_id)
    if script is None:
        raise ApiError(404, "SCRIPT_NOT_FOUND", "Script version not found")
    config = (
        body.config
        if body and body.config
        else ScriptGenerationConfig.model_validate(script.generation_config)
    )
    job, replay = await create_generation_job(
        session,
        article_id=script.article_id,
        config=config,
        idempotency_key=idempotency_key,
        source_script_version_id=script.id,
    )
    response.status_code = 200 if replay else 202
    response.headers["Location"] = f"/jobs/{job.id}"
    return JobCreateResult(job=JobOut.model_validate(job), idempotent_replay=replay)
