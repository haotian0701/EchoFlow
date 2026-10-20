from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models.articles import Article
from app.models.reviews import Approval, Evaluation
from app.models.scripts import ScriptVersion
from app.schemas.reviews import (
    ApprovalRequest,
)


async def approve_script(
    script_id: UUID,
    body: ApprovalRequest,
    session: AsyncSession,
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
        return existing, True
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
        return existing, True
    await session.refresh(approval)
    return approval, False
