from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.db.session import get_session
from app.models.articles import Article, ImportAttempt
from app.schemas.articles import (
    ArticleCreate,
    ArticleDetail,
    ArticleImport,
    ArticleList,
    ArticleWriteResult,
    ImportAttemptList,
)
from app.services.articles import import_url, store_article

router = APIRouter(prefix="/articles", tags=["articles"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

LimitParam = Annotated[int, Query(ge=1, le=100)]

OffsetParam = Annotated[int, Query(ge=0)]

SearchParam = Annotated[str | None, Query(max_length=100)]


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
    article, duplicate, attempt = await import_url(session, body)
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
