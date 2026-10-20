from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.db.session import get_session
from app.models.reviews import Approval, Evaluation
from app.models.scripts import ScriptVersion
from app.schemas.jobs import JobOut
from app.schemas.reviews import (
    ApprovalList,
    ApprovalOut,
    ApprovalRequest,
    EvaluationCreateResult,
    EvaluationDetail,
    EvaluationList,
    EvaluationSummary,
)
from app.schemas.scripts import ScriptDetail, ScriptEditRequest
from app.services.approvals import approve_script as save_script_approval
from app.services.editing import edit_script as save_script_edit
from app.services.evaluations import create_evaluation_job

router = APIRouter(tags=["script review"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("/scripts/{script_id}/evaluate", response_model=EvaluationCreateResult)
async def evaluate_script(
    script_id: UUID,
    response: Response,
    session: SessionDep,
    idempotency_key: IdempotencyKey = None,
):
    job, evaluation, replay = await create_evaluation_job(
        session, script_id=script_id, idempotency_key=idempotency_key
    )
    response.status_code = 200 if replay else 202
    response.headers["Location"] = f"/jobs/{job.id}"
    return EvaluationCreateResult(
        job=JobOut.model_validate(job),
        evaluation=EvaluationSummary.model_validate(evaluation),
        idempotent_replay=replay,
    )


@router.get("/scripts/{script_id}/evaluations", response_model=EvaluationList)
async def list_evaluations(
    script_id: UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if await session.get(ScriptVersion, script_id) is None:
        raise ApiError(404, "SCRIPT_NOT_FOUND", "Script version not found")
    total = await session.scalar(
        select(func.count())
        .select_from(Evaluation)
        .where(Evaluation.script_version_id == script_id)
    )
    items = (
        await session.scalars(
            select(Evaluation)
            .where(Evaluation.script_version_id == script_id)
            .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return EvaluationList(items=items, total=total or 0, limit=limit, offset=offset)


@router.get("/evaluations/{evaluation_id}", response_model=EvaluationDetail)
async def get_evaluation(evaluation_id: UUID, session: SessionDep):
    evaluation = await session.get(Evaluation, evaluation_id)
    if evaluation is None:
        raise ApiError(404, "EVALUATION_NOT_FOUND", "Evaluation not found")
    return evaluation


@router.get("/scripts/{script_id}/approvals", response_model=ApprovalList)
async def list_approvals(script_id: UUID, session: SessionDep):
    if await session.get(ScriptVersion, script_id) is None:
        raise ApiError(404, "SCRIPT_NOT_FOUND", "Script version not found")
    items = (
        await session.scalars(
            select(Approval)
            .where(Approval.script_version_id == script_id)
            .order_by(Approval.created_at.desc(), Approval.id.desc())
        )
    ).all()
    return ApprovalList(items=items)


@router.patch("/scripts/{script_id}", response_model=ScriptDetail, status_code=201)
async def edit_script(script_id: UUID, body: ScriptEditRequest, session: SessionDep):
    return await save_script_edit(script_id, body, session)


@router.post("/scripts/{script_id}/approve", response_model=ApprovalOut)
async def approve_script(
    script_id: UUID, body: ApprovalRequest, response: Response, session: SessionDep
):
    approval, replay = await save_script_approval(script_id, body, session)
    response.status_code = 200 if replay else 201
    return approval
