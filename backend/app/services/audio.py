import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ApiError
from app.core.fingerprints import fingerprint
from app.core.provider_errors import ProviderError
from app.integrations.provider_config import snapshot
from app.integrations.tts import FORMAT, cached_voices, get_tts_provider
from app.models.articles import Article
from app.models.audio import AudioAsset, AudioRequest
from app.models.jobs import JobEvent, ProductionJob, TaskOutbox
from app.models.reviews import Approval, Evaluation
from app.models.scripts import ScriptVersion


async def current_approval(session, script, *, lock=True):
    # All edits, evaluations and approvals synchronize on this article lock.
    if lock:
        await session.execute(
            select(Article).where(Article.id == script.article_id).with_for_update()
        )
    latest = await session.scalar(
        select(ScriptVersion.id)
        .where(ScriptVersion.article_id == script.article_id)
        .order_by(ScriptVersion.version.desc())
        .limit(1)
    )
    evaluation = await session.scalar(
        select(Evaluation)
        .where(Evaluation.script_version_id == script.id)
        .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
        .limit(1)
    )
    approval = (
        await session.scalar(
            select(Approval).where(Approval.evaluation_id == evaluation.id)
        )
        if evaluation and evaluation.status == "succeeded"
        else None
    )
    if latest != script.id or not approval:
        raise ApiError(
            409,
            "APPROVAL_STALE",
            "Only the latest script with approval of its latest successful evaluation can produce audio",
        )
    return approval


async def replay_after_conflict(session, key, requested):
    await session.rollback()
    alias = await session.get(AudioRequest, key) if key else None
    if not alias or alias.request_fingerprint != requested:
        raise ApiError(
            409, "IDEMPOTENCY_CONFLICT", "The key conflicts with another request"
        )
    job = await session.get(ProductionJob, alias.job_id)
    asset = (
        await session.get(AudioAsset, job.result_audio_asset_id)
        if job.result_audio_asset_id
        else None
    )
    return job, asset, True, True


async def create_audio_job(session, script_id, body, key):
    if key is not None and (not key.strip() or len(key.strip()) > 128):
        raise ApiError(
            422,
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key must contain 1–128 characters",
        )
    key = key.strip() if key else None
    requested = fingerprint(
        {"type": "audio_generation", "script_id": str(script_id), **body.model_dump()}
    )
    if key:
        alias = await session.get(AudioRequest, key)
        other = await session.scalar(
            select(ProductionJob).where(ProductionJob.idempotency_key == key)
        )
        if alias:
            if alias.request_fingerprint != requested:
                raise ApiError(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "This key was used for a different request",
                )
            job = await session.get(ProductionJob, alias.job_id)
            return (
                job,
                await session.get(AudioAsset, job.result_audio_asset_id)
                if job.result_audio_asset_id
                else None,
                True,
                True,
            )
        if other:
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "This key was used for a different operation",
            )
    script = await session.get(ScriptVersion, script_id)
    if not script:
        raise ApiError(404, "SCRIPT_NOT_FOUND", "Script version not found")
    if body.language != script.target_language:
        raise ApiError(
            422,
            "TTS_LANGUAGE_MISMATCH",
            "Audio language must match the script target language",
        )
    try:
        provider = get_tts_provider()
        voices, _ = await cached_voices(body.language)
    except ProviderError as exc:
        raise ApiError(503, exc.code, exc.message) from exc
    voice = next(
        (
            voice
            for voice in voices
            if voice["id"] == body.voice_id
            and body.language in voice["supported_languages"]
        ),
        None,
    )
    if not voice:
        raise ApiError(
            422,
            "TTS_VOICE_UNAVAILABLE",
            "Select an available voice compatible with the script language",
        )
    approval = await current_approval(session, script)
    config = {
        **body.model_dump(),
        "provider": provider.name,
        "model": provider.model,
        "format": FORMAT,
        "voice_name": voice["name"],
        "approval_id": str(approval.id),
    }
    audio_fingerprint = fingerprint(
        {
            "script_id": str(script_id),
            **{
                name: config[name]
                for name in (
                    "provider",
                    "model",
                    "language",
                    "voice_id",
                    "speed",
                    "format",
                )
            },
        }
    )
    candidates = (
        await session.scalars(
            select(ProductionJob)
            .where(
                ProductionJob.job_type == "audio_generation",
                ProductionJob.input_fingerprint == audio_fingerprint,
                ProductionJob.status.in_(
                    ["queued", "running", "retry_scheduled", "succeeded"]
                ),
            )
            .order_by(ProductionJob.created_at.desc())
        )
    ).all()
    job, asset = None, None
    for candidate in candidates:
        candidate_asset = (
            await session.get(AudioAsset, candidate.result_audio_asset_id)
            if candidate.result_audio_asset_id
            else None
        )
        if candidate.status != "succeeded" or (
            candidate_asset and candidate_asset.status == "available"
        ):
            job, asset = candidate, candidate_asset
            break
    reused = job is not None
    if job is None:
        job = ProductionJob(
            id=uuid.uuid4(),
            job_type="audio_generation",
            provider_snapshot=snapshot("audio_generation"),
            article_id=script.article_id,
            source_script_version_id=script.id,
            config=config,
            input_fingerprint=audio_fingerprint,
            idempotency_key=key,
            status="queued",
            stage="queued",
            attempt_count=0,
            max_attempts=3,
        )
        job.root_job_id = job.id
        session.add(job)
        try:
            await session.flush()
        except IntegrityError:
            return await replay_after_conflict(session, key, requested)
        session.add_all(
            [
                JobEvent(
                    job_id=job.id,
                    event_type="created",
                    stage="queued",
                    message="Audio production was queued",
                ),
                TaskOutbox(
                    job_id=job.id,
                    task_name="app.audio_worker.generate_audio_task",
                    queue_name="audio_generation",
                ),
            ]
        )
    if key:
        session.add(AudioRequest(key=key, request_fingerprint=requested, job_id=job.id))
    try:
        await session.commit()
    except IntegrityError:
        return await replay_after_conflict(session, key, requested)
    return job, asset, False, reused
