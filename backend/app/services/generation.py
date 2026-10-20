import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.fingerprints import fingerprint
from app.integrations.provider_config import snapshot
from app.models.articles import Article
from app.models.jobs import JobEvent, ProductionJob, TaskOutbox
from app.models.scripts import ScriptVersion
from app.schemas.scripts import ScriptGenerationConfig

TASK_NAME = "app.worker.generate_script_task"


def request_fingerprint(
    article_id: uuid.UUID,
    config: ScriptGenerationConfig,
    source_script_version_id: uuid.UUID | None,
) -> str:
    payload = {
        "article_id": str(article_id),
        "config": config.model_dump(mode="json"),
        "source_script_version_id": str(source_script_version_id)
        if source_script_version_id
        else None,
        "type": "script_generation",
    }
    return fingerprint(payload)


async def create_generation_job(
    session: AsyncSession,
    *,
    article_id: uuid.UUID,
    config: ScriptGenerationConfig,
    idempotency_key: str | None,
    source_script_version_id: uuid.UUID | None = None,
) -> tuple[ProductionJob, bool]:
    settings = get_settings()
    if settings.llm_provider == "openrouter" and not settings.openrouter_api_key:
        raise ApiError(
            503, "LLM_NOT_CONFIGURED", "OpenRouter API key is not configured"
        )
    if settings.llm_provider not in {"mock", "openrouter"}:
        raise ApiError(
            503, "LLM_NOT_CONFIGURED", "The configured LLM provider is not supported"
        )

    article = await session.get(Article, article_id)
    if article is None:
        raise ApiError(404, "ARTICLE_NOT_FOUND", "Article not found")
    if source_script_version_id:
        source = await session.get(ScriptVersion, source_script_version_id)
        if source is None or source.article_id != article_id:
            raise ApiError(404, "SCRIPT_NOT_FOUND", "Script version not found")

    key = idempotency_key.strip() if idempotency_key else None
    if key is not None and (not key or len(key) > 128):
        raise ApiError(
            422,
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key must contain 1–128 characters",
        )
    fingerprint = request_fingerprint(article_id, config, source_script_version_id)
    if key:
        existing = await session.scalar(
            select(ProductionJob).where(ProductionJob.idempotency_key == key)
        )
        if existing:
            if existing.input_fingerprint != fingerprint:
                raise ApiError(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "The Idempotency-Key was already used for a different request",
                )
            return existing, True

    job_id = uuid.uuid4()
    job = ProductionJob(
        id=job_id,
        job_type="script_generation",
        provider_snapshot=snapshot("script_generation"),
        article_id=article_id,
        source_script_version_id=source_script_version_id,
        status="queued",
        stage="queued",
        config=config.model_dump(mode="json"),
        input_fingerprint=fingerprint,
        idempotency_key=key,
        attempt_count=0,
        max_attempts=3,
    )
    job.root_job_id = job.id
    session.add(job)
    await session.flush()
    session.add_all(
        [
            JobEvent(
                job_id=job_id,
                event_type="created",
                stage="queued",
                message="Script generation was queued",
                event_metadata={"config": config.model_dump(mode="json")},
            ),
            TaskOutbox(
                job_id=job_id,
                task_name=TASK_NAME,
                queue_name="script_generation",
                status="pending",
                available_at=datetime.now(UTC),
                dispatch_attempts=0,
            ),
        ]
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if not key:
            raise
        existing = await session.scalar(
            select(ProductionJob).where(ProductionJob.idempotency_key == key)
        )
        if existing is None:
            raise
        if existing.input_fingerprint != fingerprint:
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "The Idempotency-Key conflicts with another request",
            )
        return existing, True
    await session.refresh(job)
    return job, False
