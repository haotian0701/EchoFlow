from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.errors import ApiError
from app.llm import estimated_duration_seconds
from app.models import Approval, Article, Evaluation, ScriptVersion
from app.review_service import create_evaluation_job
from app.schemas import (
    ApprovalList,
    ApprovalOut,
    ApprovalRequest,
    EvaluationCreateResult,
    EvaluationDetail,
    EvaluationList,
    EvaluationSummary,
    JobOut,
    ScriptDetail,
    ScriptEditRequest,
)

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


@router.patch("/scripts/{script_id}", response_model=ScriptDetail, status_code=201)
async def edit_script(script_id: UUID, body: ScriptEditRequest, session: SessionDep):
    script = await session.get(ScriptVersion, script_id)
    if script is None:
        raise ApiError(404, "SCRIPT_NOT_FOUND", "Script version not found")
    await session.execute(
        select(Article).where(Article.id == script.article_id).with_for_update()
    )
    latest = await session.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.article_id == script.article_id)
        .order_by(ScriptVersion.version.desc())
        .limit(1)
    )
    if (
        latest is None
        or latest.id != script.id
        or script.version != body.expected_version
    ):
        raise ApiError(
            409,
            "SCRIPT_VERSION_CONFLICT",
            "Only the current latest script version can be edited",
            details={"latest_version": latest.version if latest else None},
        )
    if body.text == script.text.strip():
        raise ApiError(422, "SCRIPT_UNCHANGED", "The edited script has not changed")
    edited = ScriptVersion(
        article_id=script.article_id,
        version=script.version + 1,
        text=body.text,
        source="edited",
        parent_version_id=script.id,
        target_language=script.target_language,
        generation_config=script.generation_config,
        provider="human",
        requested_model="manual-editor",
        actual_model="manual-editor",
        prompt_version="manual-edit-v1",
        estimated_duration_seconds=estimated_duration_seconds(
            body.text, script.target_language
        ),
    )
    session.add(edited)
    await session.commit()
    await session.refresh(edited)
    return edited


@router.post("/scripts/{script_id}/approve", response_model=ApprovalOut)
async def approve_script(
    script_id: UUID,
    body: ApprovalRequest,
    response: Response,
    session: SessionDep,
):
    script = await session.get(ScriptVersion, script_id)
    if script is None:
        raise ApiError(404, "SCRIPT_NOT_FOUND", "Script version not found")
    await session.execute(
        select(Article).where(Article.id == script.article_id).with_for_update()
    )
    latest_script = await session.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.article_id == script.article_id)
        .order_by(ScriptVersion.version.desc())
        .limit(1)
    )
    if (
        latest_script is None
        or latest_script.id != script.id
        or body.expected_version != script.version
    ):
        raise ApiError(
            409,
            "SCRIPT_VERSION_CONFLICT",
            "Only the current latest script version can be approved",
        )
    latest_evaluation = await session.scalar(
        select(Evaluation)
        .where(Evaluation.script_version_id == script.id)
        .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
        .limit(1)
    )
    if latest_evaluation is None:
        raise ApiError(
            409, "EVALUATION_REQUIRED", "Evaluate this script before approval"
        )
    if latest_evaluation.status != "succeeded":
        raise ApiError(
            409,
            "EVALUATION_NOT_READY",
            "The latest evaluation must succeed before approval",
        )
    if latest_evaluation.id != body.evaluation_id:
        raise ApiError(
            409,
            "EVALUATION_STALE",
            "Approval must reference the latest evaluation",
        )

    submitted = {str(issue_id) for issue_id in body.acknowledged_critical_issue_ids}
    known = {str(issue["id"]) for issue in latest_evaluation.issues}
    critical = {
        str(issue["id"])
        for issue in latest_evaluation.issues
        if issue.get("severity") == "critical"
    }
    unknown = submitted - known
    if unknown:
        raise ApiError(
            422,
            "UNKNOWN_ISSUE_ACKNOWLEDGEMENT",
            "One or more acknowledged issues do not belong to this evaluation",
            details={"issue_ids": sorted(unknown)},
        )
    missing = critical - submitted
    if missing:
        raise ApiError(
            409,
            "CRITICAL_ISSUES_UNACKNOWLEDGED",
            "Confirm every critical issue before approval",
            details={"issue_ids": sorted(missing)},
        )

    existing = await session.scalar(
        select(Approval).where(Approval.evaluation_id == latest_evaluation.id)
    )
    if existing:
        response.status_code = 200
        return existing
    approval = Approval(
        script_version_id=script.id,
        evaluation_id=latest_evaluation.id,
        acknowledged_critical_issue_ids=sorted(submitted),
    )
    session.add(approval)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(Approval).where(Approval.evaluation_id == latest_evaluation.id)
        )
        if existing is None:
            raise
        response.status_code = 200
        return existing
    await session.refresh(approval)
    response.status_code = 201
    return approval


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
