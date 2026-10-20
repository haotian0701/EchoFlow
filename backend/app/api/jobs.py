from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.presenters.jobs import decorate
from app.core.errors import ApiError
from app.db.session import get_session
from app.models.articles import Article
from app.models.jobs import JobEvent, JobExecution, ProductionJob, ProviderCall
from app.models.scripts import ScriptVersion
from app.schemas.jobs import (
    ExecutionOut,
    JobCreate,
    JobCreateResult,
    JobEventList,
    JobList,
    JobOut,
    JobRetryBody,
    ProviderCallOut,
)
from app.schemas.scripts import (
    RegenerateRequest,
    ScriptDetail,
    ScriptGenerationConfig,
    ScriptList,
)
from app.services.generation import create_generation_job
from app.services.retries import retry_job

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
    q: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    article_id: UUID | None = None,
    script_id: UUID | None = None,
    job_type: Annotated[
        Literal["script_generation", "script_evaluation", "audio_generation"] | None,
        Query(alias="type"),
    ] = None,
    status: Literal[
        "queued", "running", "retry_scheduled", "succeeded", "failed", "needs_attention"
    ]
    | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    filters = []
    if q:
        filters.append(
            ProductionJob.article_id.in_(
                select(Article.id).where(Article.title.ilike(f"%{q}%"))
            )
        )
    if created_from:
        filters.append(ProductionJob.created_at >= created_from)
    if created_to:
        filters.append(ProductionJob.created_at < created_to)
    if article_id:
        filters.append(ProductionJob.article_id == article_id)
    if script_id:
        filters.append(ProductionJob.source_script_version_id == script_id)
    if job_type:
        filters.append(ProductionJob.job_type == job_type)
    if status:
        filters.append(ProductionJob.status == status)
    total = await session.scalar(
        select(func.count()).select_from(ProductionJob).where(*filters)
    )
    items = (
        await session.scalars(
            select(ProductionJob)
            .where(*filters)
            .order_by(ProductionJob.created_at.desc(), ProductionJob.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return JobList(
        items=[await decorate(session, job) for job in items],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: UUID, session: SessionDep):
    job = await session.get(ProductionJob, job_id)
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", "Job not found")
    return await decorate(session, job)


@router.get("/jobs/{job_id}/events", response_model=JobEventList)
async def get_job_events(
    job_id: UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if await session.get(ProductionJob, job_id) is None:
        raise ApiError(404, "JOB_NOT_FOUND", "Job not found")
    items = (
        await session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id)
            .order_by(JobEvent.created_at, JobEvent.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return JobEventList(
        items=items,
        total=await session.scalar(
            select(func.count()).select_from(JobEvent).where(JobEvent.job_id == job_id)
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/articles/{article_id}/scripts", response_model=ScriptList)
async def list_scripts(
    article_id: UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    total = await session.scalar(
        select(func.count())
        .select_from(ScriptVersion)
        .where(ScriptVersion.article_id == article_id)
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


@router.post("/jobs/{job_id}/retry")
async def retry_job_endpoint(
    job_id: UUID,
    body: JobRetryBody,
    response: Response,
    session: SessionDep,
    idempotency_key: IdempotencyKey = None,
):
    job, replay = await retry_job(
        session, job_id, body.acknowledge_possible_charge, idempotency_key
    )
    response.status_code = 200 if replay else 202
    response.headers["Location"] = f"/jobs/{job.id}"
    mode = "resume_upload" if job.config.get("resume_artifact_id") else "new_call"
    return {
        "job": await decorate(session, job),
        "idempotent_replay": replay,
        "retry_mode": mode,
    }


@router.get("/jobs/{job_id}/executions")
async def job_executions(
    job_id: UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return await record_page(session, job_id, JobExecution, ExecutionOut, limit, offset)


@router.get("/jobs/{job_id}/calls")
async def job_calls(
    job_id: UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return await record_page(
        session, job_id, ProviderCall, ProviderCallOut, limit, offset
    )


async def record_page(session, job_id, model, output, limit, offset):
    if not await session.get(ProductionJob, job_id):
        raise ApiError(404, "JOB_NOT_FOUND", "Job not found")
    timestamp = model.started_at if model is JobExecution else model.created_at
    items = (
        await session.scalars(
            select(model)
            .where(model.job_id == job_id)
            .order_by(timestamp, model.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "items": [output.model_validate(item) for item in items],
        "limit": limit,
        "offset": offset,
        "total": await session.scalar(
            select(func.count()).select_from(model).where(model.job_id == job_id)
        ),
    }
