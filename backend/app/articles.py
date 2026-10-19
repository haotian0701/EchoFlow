import hashlib
import unicodedata
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.errors import ApiError
from app.importer import import_article
from app.models import Article, ImportAttempt
from app.schemas import (
    ArticleCreate,
    ArticleDetail,
    ArticleImport,
    ArticleList,
    ArticleWriteResult,
    ImportAttemptList,
)

router = APIRouter(prefix="/articles", tags=["articles"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LimitParam = Annotated[int, Query(ge=1, le=100)]
OffsetParam = Annotated[int, Query(ge=0)]
SearchParam = Annotated[str | None, Query(max_length=100)]


def content_hash(text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFC", text).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def store_article(
    session: AsyncSession,
    *,
    title: str,
    source: str,
    source_url: str | None,
    text: str,
    language: str,
    import_type: str,
    attempted_url: str | None = None,
) -> tuple[Article, bool, ImportAttempt | None]:
    digest = content_hash(text)
    existing = await session.scalar(
        select(Article).where(Article.content_hash == digest)
    )
    duplicate = existing is not None
    article = existing or Article(
        title=title,
        source=source,
        source_url=source_url,
        original_text=text,
        language=language,
        import_type=import_type,
        content_hash=digest,
    )
    if not duplicate:
        session.add(article)
    attempt = None
    if attempted_url is not None:
        attempt = ImportAttempt(
            url=attempted_url,
            status="duplicate" if duplicate else "succeeded",
            article=article,
        )
        session.add(attempt)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        article = await session.scalar(
            select(Article).where(Article.content_hash == digest)
        )
        if article is None:
            raise
        duplicate = True
        if attempted_url is not None:
            attempt = ImportAttempt(
                url=attempted_url, status="duplicate", article=article
            )
            session.add(attempt)
            await session.commit()
    await session.refresh(article)
    if attempt is not None:
        await session.refresh(attempt)
    return article, duplicate, attempt


async def record_failed_import(
    session: AsyncSession, url: str, error: ApiError
) -> ImportAttempt:
    attempt = ImportAttempt(
        url=url, status="failed", error_code=error.code, error_message=error.message
    )
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    return attempt


@router.post("", response_model=ArticleWriteResult)
async def create_article(body: ArticleCreate, response: Response, session: SessionDep):
    article, duplicate, _ = await store_article(
        session,
        title=body.title,
        source=body.source,
        source_url=None,
        text=body.text,
        language=body.language,
        import_type="manual",
    )
    response.status_code = 200 if duplicate else 201
    return ArticleWriteResult(
        article=ArticleDetail.model_validate(article), duplicate=duplicate
    )


@router.post("/import", response_model=ArticleWriteResult)
async def import_article_url(
    body: ArticleImport, response: Response, session: SessionDep
):
    try:
        extracted = await import_article(body.url)
        title = body.title or extracted.title
        if not title or not title.strip():
            raise ApiError(
                502,
                "EXTRACTION_FAILED",
                "No article title was found; enter a title and retry or paste the text",
            )
        if len(title) > 300:
            raise ApiError(
                422, "INVALID_ARTICLE", "The article title exceeds 300 characters"
            )
        source = body.source or httpx.URL(extracted.final_url).host
        if len(source) > 200:
            raise ApiError(
                422, "INVALID_ARTICLE", "The article source exceeds 200 characters"
            )
        article, duplicate, attempt = await store_article(
            session,
            title=title.strip(),
            source=source,
            source_url=extracted.final_url,
            text=extracted.text,
            language=body.language,
            import_type="url",
            attempted_url=body.url,
        )
    except ApiError as error:
        attempt = await record_failed_import(session, body.url, error)
        error.details["import_attempt_id"] = str(attempt.id)
        raise
    response.status_code = 200 if duplicate else 201
    return ArticleWriteResult(
        article=ArticleDetail.model_validate(article),
        duplicate=duplicate,
        import_attempt=attempt,
    )


@router.get("/imports", response_model=ImportAttemptList)
async def list_imports(
    session: SessionDep,
    limit: LimitParam = 20,
    offset: OffsetParam = 0,
):
    total = await session.scalar(select(func.count()).select_from(ImportAttempt))
    items = (
        await session.scalars(
            select(ImportAttempt)
            .order_by(ImportAttempt.created_at.desc(), ImportAttempt.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return ImportAttemptList(items=items, total=total or 0, limit=limit, offset=offset)


@router.get("", response_model=ArticleList)
async def list_articles(
    session: SessionDep,
    limit: LimitParam = 20,
    offset: OffsetParam = 0,
    q: SearchParam = None,
):
    filter_clause = Article.title.ilike(f"%{q.strip()}%") if q and q.strip() else None
    count_query = select(func.count()).select_from(Article)
    items_query = (
        select(Article)
        .order_by(Article.created_at.desc(), Article.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if filter_clause is not None:
        count_query = count_query.where(filter_clause)
        items_query = items_query.where(filter_clause)
    total = await session.scalar(count_query)
    items = (await session.scalars(items_query)).all()
    return ArticleList(items=items, total=total or 0, limit=limit, offset=offset)


@router.get("/{article_id}", response_model=ArticleDetail)
async def get_article(article_id: UUID, session: SessionDep):
    article = await session.get(Article, article_id)
    if article is None:
        raise ApiError(404, "ARTICLE_NOT_FOUND", "Article not found")
    return article
