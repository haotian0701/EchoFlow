import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import ApiError
from app.evaluation_engine import EVALUATION_PROMPT_VERSION, RULE_VERSION
from app.models import (
    Article,
    Evaluation,
    JobEvent,
    ProductionJob,
    ScriptVersion,
    TaskOutbox,
)

EVALUATION_TASK_NAME = "app.worker.evaluate_script_task"


def evaluation_fingerprint(script: ScriptVersion) -> str:
    payload = {
        "type": "script_evaluation",
        "script_version_id": str(script.id),
        "rule_version": RULE_VERSION,
        "prompt_version": EVALUATION_PROMPT_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_provider_configuration() -> None:
    settings = get_settings()
    if settings.llm_provider == "openrouter" and not settings.openrouter_api_key:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "OpenRouter API key is not configured")
    if settings.llm_provider not in {"mock", "openrouter"}:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "The configured LLM provider is not supported")


async def create_evaluation_job(
    session: AsyncSession,
    *,
    script_id: uuid.UUID,
    idempotency_key: str | None,
) -> tuple[ProductionJob, Evaluation, bool]:
    validate_provider_configuration()
    script = await session.get(ScriptVersion, script_id)
    if script is None:
        raise ApiError(404, "SCRIPT_NOT_FOUND", "Script version not found")
    await session.execute(select(Article).where(Article.id == script.article_id).with_for_update())

    key = idempotency_key.strip() if idempotency_key else None
    if key is not None and (not key or len(key) > 128):
        raise ApiError(
            422,
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key must contain 1–128 characters",
        )
    fingerprint = evaluation_fingerprint(script)
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
            evaluation = await session.get(Evaluation, existing.result_evaluation_id)
            if evaluation is None:
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "The original evaluation is unavailable")
            return existing, evaluation, True

    now = datetime.now(UTC)
    job = ProductionJob(
        job_type="script_evaluation",
        article_id=script.article_id,
        source_script_version_id=script.id,
        status="queued",
        stage="queued",
        config={
            "rule_version": RULE_VERSION,
            "prompt_version": EVALUATION_PROMPT_VERSION,
        },
        input_fingerprint=fingerprint,
        idempotency_key=key,
        attempt_count=0,
        max_attempts=3,
    )
    session.add(job)
    await session.flush()
    evaluation = Evaluation(
        script_version_id=script.id,
        job_id=job.id,
        status="queued",
        issues=[],
        rule_version=RULE_VERSION,
        prompt_version=EVALUATION_PROMPT_VERSION,
    )
    session.add(evaluation)
    await session.flush()
    job.result_evaluation_id = evaluation.id
    session.add_all(
        [
            JobEvent(
                job_id=job.id,
                event_type="created",
                stage="queued",
                message="Script evaluation was queued",
                event_metadata={"script_version_id": str(script.id)},
            ),
            TaskOutbox(
                job_id=job.id,
                task_name=EVALUATION_TASK_NAME,
                queue_name="script_evaluation",
                status="pending",
                available_at=now,
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
        if existing is None or existing.input_fingerprint != fingerprint:
            raise ApiError(
                409, "IDEMPOTENCY_CONFLICT", "The Idempotency-Key conflicts with another request"
            )
        evaluation = await session.get(Evaluation, existing.result_evaluation_id)
        if evaluation is None:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "The original evaluation is unavailable")
        return existing, evaluation, True
    await session.refresh(job)
    await session.refresh(evaluation)
    return job, evaluation, False
