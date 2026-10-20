from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.script_rules import estimated_duration_seconds
from app.models.articles import Article
from app.models.scripts import ScriptVersion
from app.schemas.scripts import ScriptEditRequest


async def edit_script(script_id: UUID, body: ScriptEditRequest, session: AsyncSession):
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
