import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ApiError
from app.core.fingerprints import fingerprint
from app.core.provider_errors import ProviderError
from app.core.time import utc
from app.evaluation.contracts import EVALUATION_PROMPT_VERSION, RULE_VERSION
from app.integrations.provider_config import snapshot
from app.integrations.tts import cached_voices, get_tts_provider
from app.models.audio import AudioAsset, AudioRequest
from app.models.jobs import JobRetryRequest, ProductionJob, ProviderCall, TaskOutbox
from app.models.reviews import Evaluation
from app.models.scripts import ScriptVersion
from app.services.artifacts import find_artifact
from app.services.audio import current_approval
from app.services.evaluations import validate_provider_configuration
from app.services.execution import event, lock_job


async def capabilities(session, job):
    child = await session.scalar(
        select(ProductionJob.id).where(ProductionJob.retry_of_job_id == job.id)
    )
    artifact = await find_artifact(session, job)
    ready = bool(
        artifact
        and artifact.status == "ready"
        and artifact.sha256
        and utc(artifact.expires_at) > datetime.now(UTC)
    )
    calls = (
        await session.scalars(select(ProviderCall).where(ProviderCall.job_id == job.id))
    ).all()
    charge = not ready and (
        job.status == "needs_attention"
        or any(
            c.uncertain
            or c.status in ("unknown", "running", "succeeded")
            or c.error_code
            in (
                "LLM_OUTPUT_TRUNCATED",
                "INVALID_PROVIDER_RESPONSE",
                "INVALID_TTS_AUDIO",
                "TTS_AUDIO_TOO_LARGE",
            )
            for c in calls
        )
    )
    eligible = job.status in ("failed", "needs_attention") and child is None
    reason = None
    if (
        eligible
        and job.job_type in ("script_evaluation", "audio_generation")
        and not ready
    ):
        script = await session.get(ScriptVersion, job.source_script_version_id)
        latest = await session.scalar(
            select(ScriptVersion.id)
            .where(ScriptVersion.article_id == job.article_id)
            .order_by(ScriptVersion.version.desc())
            .limit(1)
        )
        if not script or script.id != latest:
            eligible, reason = (
                False,
                "This version is superseded. Return to the current script.",
            )
        elif job.job_type == "script_evaluation":
            evaluation = await session.scalar(
                select(Evaluation)
                .where(Evaluation.script_version_id == script.id)
                .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
                .limit(1)
            )
            if (
                not evaluation
                or evaluation.job_id != job.id
                or evaluation.status not in ("failed", "needs_attention")
            ):
                eligible, reason = (
                    False,
                    "Only the latest failed evaluation can be retried. Open Script Review.",
                )
        else:
            try:
                await current_approval(session, script, lock=False)
            except ApiError:
                eligible, reason = (
                    False,
                    "Approve the current script and its latest evaluation before a new synthesis call.",
                )
    return {
        "can_retry": eligible,
        "retry_mode": "resume_upload" if ready else "new_call",
        "requires_charge_acknowledgement": charge,
        "retry_child_job_id": child,
        "retry_unavailable_reason": reason,
        "recovery_expires_at": artifact.expires_at if ready else None,
        "resume_artifact_id": artifact.id if ready else None,
    }


async def _retry_job(session, job_id, acknowledge, key):
    if key is not None and (not key.strip() or len(key) > 128):
        raise ApiError(
            422, "INVALID_IDEMPOTENCY_KEY", "Use an Idempotency-Key of 1–128 characters"
        )
    key = key.strip() if key else None
    requested = fingerprint(
        {"job_id": str(job_id), "acknowledge_possible_charge": acknowledge}
    )
    parent = await lock_job(session, job_id)
    if not parent:
        raise ApiError(404, "JOB_NOT_FOUND", "Job not found")
    if key:
        saved = await session.get(JobRetryRequest, key)
        if saved:
            if saved.request_fingerprint != requested:
                raise ApiError(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "This key belongs to another retry request",
                )
            return await session.get(ProductionJob, saved.job_id), True
        if await session.get(AudioRequest, key) or await session.scalar(
            select(ProductionJob.id).where(ProductionJob.idempotency_key == key)
        ):
            raise ApiError(
                409, "IDEMPOTENCY_CONFLICT", "This key belongs to another operation"
            )
    if parent.status not in ("failed", "needs_attention"):
        raise ApiError(
            409,
            "JOB_NOT_RETRYABLE",
            "Only failed jobs or jobs needing attention can be retried",
        )
    child = await session.scalar(
        select(ProductionJob).where(ProductionJob.retry_of_job_id == parent.id)
    )
    if child:
        if key:
            session.add(
                JobRetryRequest(key=key, request_fingerprint=requested, job_id=child.id)
            )
            await session.commit()
        return child, True
    ability = await capabilities(session, parent)
    if not ability["can_retry"]:
        raise ApiError(
            409,
            "RETRY_INPUT_STALE",
            ability["retry_unavailable_reason"]
            or "Return to the business page to create a new task",
        )
    if ability["requires_charge_acknowledgement"] and not acknowledge:
        raise ApiError(
            409,
            "CHARGE_ACKNOWLEDGEMENT_REQUIRED",
            "The previous call may have been charged. Confirm the risk before calling the provider again.",
        )
    config = {k: v for k, v in parent.config.items() if k != "resume_artifact_id"}
    saved_snapshot = snapshot(parent.job_type)
    if ability["retry_mode"] == "resume_upload":
        config["resume_artifact_id"] = str(ability["resume_artifact_id"])
        saved_snapshot = dict(parent.provider_snapshot)
    elif parent.job_type == "audio_generation":
        script = await session.get(ScriptVersion, parent.source_script_version_id)
        approval = await current_approval(session, script)
        try:
            provider = get_tts_provider()
            voices, _ = await cached_voices(config["language"])
        except ProviderError as error:
            raise ApiError(503, error.code, error.message) from error
        voice = next(
            (
                v
                for v in voices
                if v["id"] == config["voice_id"]
                and config["language"] in v["supported_languages"]
            ),
            None,
        )
        if not voice:
            raise ApiError(
                409,
                "TTS_VOICE_UNAVAILABLE",
                "Choose a compatible voice in Audio Studio",
            )
        config.update(
            provider=provider.name,
            model=provider.model,
            voice_name=voice["name"],
            approval_id=str(approval.id),
        )
    else:
        validate_provider_configuration()
    input_hash = parent.input_fingerprint
    if parent.job_type == "audio_generation" and ability["retry_mode"] == "new_call":
        input_hash = fingerprint(
            {
                "script_id": str(parent.source_script_version_id),
                **{
                    k: config[k]
                    for k in (
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
        equivalents = (
            await session.scalars(
                select(ProductionJob).where(
                    ProductionJob.input_fingerprint == input_hash,
                    ProductionJob.job_type == "audio_generation",
                    ProductionJob.status.in_(
                        ["queued", "running", "retry_scheduled", "succeeded"]
                    ),
                )
            )
        ).all()
        for equivalent in equivalents:
            asset = (
                await session.get(AudioAsset, equivalent.result_audio_asset_id)
                if equivalent.result_audio_asset_id
                else None
            )
            if equivalent.status != "succeeded" or (
                asset and asset.status == "available"
            ):
                raise ApiError(
                    409,
                    "AUDIO_ALREADY_EXISTS",
                    "Reuse the existing audio or active task",
                    {
                        "job_id": str(equivalent.id),
                        "location": f"/audio/{asset.id}"
                        if asset
                        else f"/jobs/{equivalent.id}",
                    },
                )
    child = ProductionJob(
        id=uuid.uuid4(),
        job_type=parent.job_type,
        article_id=parent.article_id,
        source_script_version_id=parent.source_script_version_id,
        retry_of_job_id=parent.id,
        root_job_id=parent.root_job_id or parent.id,
        provider_snapshot=saved_snapshot,
        config=config,
        input_fingerprint=input_hash,
        status="queued",
        stage="queued",
        attempt_count=0,
        max_attempts=3,
    )
    session.add(child)
    await session.flush()
    if parent.job_type == "script_evaluation":
        evaluation = Evaluation(
            script_version_id=child.source_script_version_id,
            job_id=child.id,
            status="queued",
            issues=[],
            rule_version=RULE_VERSION,
            prompt_version=EVALUATION_PROMPT_VERSION,
        )
        session.add(evaluation)
        await session.flush()
        child.result_evaluation_id = evaluation.id
    task = {
        "script_generation": "app.worker.generate_script_task",
        "script_evaluation": "app.worker.evaluate_script_task",
        "audio_generation": "app.audio_worker.generate_audio_task",
    }[child.job_type]
    session.add(TaskOutbox(job_id=child.id, task_name=task, queue_name=child.job_type))
    event(
        session,
        child,
        "manual_retry",
        "Manual retry queued",
        retry_of_job_id=str(parent.id),
        retry_mode=ability["retry_mode"],
    )
    if key:
        session.add(
            JobRetryRequest(key=key, request_fingerprint=requested, job_id=child.id)
        )
    await session.commit()
    return child, False


async def retry_job(session, job_id, acknowledge, key):
    try:
        return await _retry_job(session, job_id, acknowledge, key)
    except IntegrityError:
        await session.rollback()
        # Two different parent locks can race on a globally unique request key.
        saved = await session.get(JobRetryRequest, key.strip()) if key else None
        requested = fingerprint(
            {"job_id": str(job_id), "acknowledge_possible_charge": acknowledge}
        )
        if saved and saved.request_fingerprint == requested:
            return await session.get(ProductionJob, saved.job_id), True
        raise ApiError(
            409, "IDEMPOTENCY_CONFLICT", "This key conflicts with a concurrent request"
        ) from None
