import asyncio
import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio_service import create_audio_job
from app.database import get_session
from app.errors import ApiError
from app.llm import ProviderError
from app.models import Article, AudioAsset, ProductionJob, ProviderCall, ScriptVersion
from app.schemas import (
    AudioCreate,
    AudioCreateResult,
    AudioDetail,
    AudioList,
    AudioOut,
    JobOut,
    Language,
    TTSVoiceList,
)
from app.storage import AudioStorage, get_audio_storage
from app.tts import cached_voices

router = APIRouter(tags=["audio production"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
StorageDep = Annotated[AudioStorage, Depends(get_audio_storage)]


async def audio_out(session, asset):
    article = await session.get(Article, asset.article_id)
    script = await session.get(ScriptVersion, asset.script_version_id)
    return AudioOut.model_validate(asset).model_copy(
        update={"article_title": article.title, "script_version": script.version}
    )


@router.get("/tts/voices", response_model=TTSVoiceList)
async def voices(language: Language):
    try:
        items, cached = await cached_voices(language)
        return {"items": items, "cached": cached}
    except ProviderError as exc:
        raise ApiError(503, exc.code, exc.message) from exc


@router.post("/scripts/{script_id}/audio", response_model=AudioCreateResult)
async def create_audio(
    script_id: UUID,
    body: AudioCreate,
    response: Response,
    session: SessionDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    job, asset, replay, reused = await create_audio_job(
        session, script_id, body, idempotency_key
    )
    response.status_code = 200 if replay or reused else 202
    response.headers["Location"] = f"/jobs/{job.id}"
    return AudioCreateResult(
        job=JobOut.model_validate(job),
        audio=await audio_out(session, asset) if asset else None,
        idempotent_replay=replay,
        reused_existing=reused,
    )


@router.get("/audio", response_model=AudioList)
async def list_audio(
    session: SessionDep,
    q: str = "",
    article_id: UUID | None = None,
    script_id: UUID | None = None,
    status: Literal["available", "deleting", "delete_failed", "deleted"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    filters = (
        [AudioAsset.status == status] if status else [AudioAsset.status != "deleted"]
    )
    if q.strip():
        filters.append(Article.title.icontains(q.strip(), autoescape=True))
    if article_id:
        filters.append(AudioAsset.article_id == article_id)
    if script_id:
        filters.append(AudioAsset.script_version_id == script_id)
    base = (
        select(AudioAsset, Article.title, ScriptVersion.version)
        .join(Article, Article.id == AudioAsset.article_id)
        .join(ScriptVersion, ScriptVersion.id == AudioAsset.script_version_id)
        .where(*filters)
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await session.execute(
            base.order_by(AudioAsset.created_at.desc(), AudioAsset.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return AudioList(
        items=[
            AudioOut.model_validate(asset).model_copy(
                update={"article_title": title, "script_version": version}
            )
            for asset, title, version in rows
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def find_audio(session, audio_id, *, lock=False):
    asset = await session.get(AudioAsset, audio_id, with_for_update=lock)
    if not asset:
        raise ApiError(404, "AUDIO_NOT_FOUND", "Audio not found")
    return asset


@router.get("/audio/{audio_id}", response_model=AudioDetail)
async def get_audio(audio_id: UUID, session: SessionDep):
    asset = await find_audio(session, audio_id)
    summary = await audio_out(session, asset)
    return AudioDetail(
        **summary.model_dump(),
        article=await session.get(Article, asset.article_id),
        job=await session.get(ProductionJob, asset.job_id),
        provider_calls=(
            await session.scalars(
                select(ProviderCall)
                .where(ProviderCall.job_id == asset.job_id)
                .order_by(ProviderCall.created_at)
            )
        ).all(),
    )


def byte_range(value: str | None, size: int) -> tuple[int, int]:
    if value is None:
        return 0, size - 1
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()):
        raise ApiError(
            416,
            "INVALID_AUDIO_RANGE",
            "Only a single byte range is supported",
            {"size": size},
        )
    left, right = match.groups()
    if left:
        start, end = int(left), min(int(right), size - 1) if right else size - 1
    else:
        start, end = max(0, size - int(right)), size - 1
    if start > end or start >= size:
        raise ApiError(
            416,
            "INVALID_AUDIO_RANGE",
            "The requested byte range is outside this file",
            {"size": size},
        )
    return start, end


async def audio_response(session, asset, storage, range_header=None, download=False):
    if asset.status == "deleted":
        raise ApiError(410, "AUDIO_DELETED", "This audio has been deleted")
    if asset.status != "available":
        raise ApiError(
            409,
            "AUDIO_UNAVAILABLE",
            "Audio deletion is pending; retry deletion from the library",
        )
    start, end = byte_range(range_header, asset.file_size)
    try:
        stream = await asyncio.to_thread(
            storage.open, asset.bucket, asset.object_key, start, end - start + 1
        )
    except Exception as exc:
        raise ApiError(
            503,
            "AUDIO_STORAGE_UNAVAILABLE",
            "The audio file is temporarily unavailable",
        ) from exc

    async def chunks():
        try:
            while True:
                chunk = await asyncio.to_thread(stream.read, 65536)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(stream.close)
            if hasattr(stream, "release_conn"):
                await asyncio.to_thread(stream.release_conn)

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store",
    }
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{asset.file_size}"
    if download:
        article = await session.get(Article, asset.article_id)
        script = await session.get(ScriptVersion, asset.script_version_id)
        name = (
            re.sub(r"[^\w .-]", "_", article.title, flags=re.UNICODE)[:100].strip(" .")
            or "EchoFlow"
        )
        name += f"-v{script.version}-{asset.voice_id}.mp3"
        headers["Content-Disposition"] = (
            f"attachment; filename=\"echoflow-v{script.version}.mp3\"; filename*=UTF-8''{quote(name)}"
        )
    return StreamingResponse(
        chunks(),
        status_code=206 if range_header else 200,
        media_type="audio/mpeg",
        headers=headers,
    )


@router.get("/audio/{audio_id}/stream")
async def stream_audio(
    audio_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
):
    return await audio_response(
        session, await find_audio(session, audio_id), storage, range_header
    )


@router.get("/audio/{audio_id}/download")
async def download_audio(audio_id: UUID, session: SessionDep, storage: StorageDep):
    return await audio_response(
        session, await find_audio(session, audio_id), storage, download=True
    )


@router.delete("/audio/{audio_id}", status_code=204)
async def delete_audio(audio_id: UUID, session: SessionDep, storage: StorageDep):
    asset = await find_audio(session, audio_id, lock=True)
    if asset.status == "deleted":
        return Response(status_code=204)
    asset.status, asset.delete_error = "deleting", None
    await session.commit()
    try:
        await asyncio.to_thread(storage.delete, asset.bucket, asset.object_key)
    except Exception as exc:
        asset.status, asset.delete_error = (
            "delete_failed",
            "Storage deletion failed; retry deleting this audio",
        )
        await session.commit()
        raise ApiError(503, "AUDIO_DELETE_FAILED", asset.delete_error) from exc
    asset.status, asset.deleted_at = "deleted", datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)
