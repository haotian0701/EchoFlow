import hashlib
import unicodedata

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.integrations.article_import import import_article
from app.models.articles import Article, ImportAttempt


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


async def import_url(session, body):
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
    return article, duplicate, attempt
