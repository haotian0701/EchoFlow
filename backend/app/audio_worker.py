import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.audio_service import current_approval
from app.audio_staging import checkpoint, paths, remove, verify
from app.celery_app import celery_app
from app.config import get_settings
from app.database import SessionLocal
from app.errors import ApiError
from app.execution import (
    apply_call_metadata,
    event,
    execution_scope,
    fenced,
    finish,
    record_failure,
)
from app.llm import ProviderError
from app.models import (
    Approval,
    AudioArtifact,
    AudioAsset,
    ProductionJob,
    ProviderCall,
    ScriptVersion,
)
from app.provider_config import execution_settings
from app.storage import get_audio_storage
from app.tts import get_tts_provider
from app.worker import run_task


async def find_artifact(session, job):
    artifact_id = job.config.get("resume_artifact_id")
    if artifact_id:
        return await session.get(AudioArtifact, uuid.UUID(artifact_id))
    return await session.scalar(
        select(AudioArtifact)
        .where(AudioArtifact.job_id == job.id)
        .order_by(AudioArtifact.created_at.desc())
        .limit(1)
    )


async def upload_artifact(job_id, execution_id, artifact, factory, storage=None):
    metadata = await asyncio.to_thread(verify, artifact)
    if metadata is None:
        raise ProviderError(
            "AUDIO_CHECKPOINT_INVALID",
            "The audio checkpoint is missing, expired, or damaged",
            ambiguous=True,
        )
    async with factory() as session:
        call = await session.get(ProviderCall, artifact.provider_call_id)
        source = await session.get(
            ScriptVersion,
            (
                await session.get(ProductionJob, artifact.job_id)
            ).source_script_version_id,
        )
        approval = await session.get(Approval, artifact.approval_id)
        if (
            not call
            or call.job_id != artifact.job_id
            or str(call.execution_id) != metadata.get("execution_id")
            or not approval
            or approval.script_version_id != source.id
        ):
            raise ProviderError(
                "AUDIO_CHECKPOINT_INVALID",
                "Saved audio metadata does not match its original synthesis",
                ambiguous=True,
            )
    path, _ = paths(artifact)
    store = storage or get_audio_storage()
    # Fixed key makes an upload after a previous successful PUT safe to repeat.
    for attempt in range(3):
        try:
            await asyncio.to_thread(
                store.upload, artifact.bucket, artifact.object_key, path
            )
            break
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(1 if attempt == 0 else 3)
    async with factory() as session:
        job = await fenced(session, job_id, execution_id)
        if not job:
            return
        saved = await session.get(AudioArtifact, artifact.id)
        script = await session.get(ScriptVersion, job.source_script_version_id)
        call = await session.get(ProviderCall, artifact.provider_call_id)
        apply_call_metadata(call, metadata)
        call.audio_duration_seconds = metadata["duration_seconds"]
        call.status, call.completed_at, call.uncertain = (
            "succeeded",
            call.completed_at or datetime.now(UTC),
            False,
        )
        asset = await session.get(AudioAsset, artifact.audio_id)
        if asset is None:
            config = job.config
            asset = AudioAsset(
                id=artifact.audio_id,
                article_id=job.article_id,
                script_version_id=script.id,
                approval_id=artifact.approval_id,
                job_id=job_id,
                provider=call.provider,
                model=call.actual_model or call.requested_model,
                language=config["language"],
                voice_id=config["voice_id"],
                voice_name=config["voice_name"],
                speed=config["speed"],
                format=config["format"],
                bucket=artifact.bucket,
                object_key=artifact.object_key,
                duration_seconds=metadata["duration_seconds"],
                file_size=metadata["file_size"],
                input_characters=len(script.text),
            )
            session.add(asset)
            await session.flush()
        saved.status, saved.completed_at = "completed", datetime.now(UTC)
        saved.sha256, saved.file_size, saved.duration_seconds = (
            metadata["sha256"],
            metadata["file_size"],
            metadata["duration_seconds"],
        )
        saved.last_error = None
        job.result_audio_asset_id = asset.id
        await finish(session, job)
        event(session, job, "succeeded", "MP3 audio is ready to play and download")
        await session.commit()
    await asyncio.to_thread(remove, artifact)


async def process_audio_job(
    job_id, *, session_factory=SessionLocal, provider=None, storage=None
):
    async with execution_scope(
        session_factory, job_id, "audio_generation"
    ) as execution_id:
        if execution_id is None:
            return
        call_id = None
        async with session_factory() as session:
            job = await fenced(session, job_id, execution_id)
            if not job:
                return
            artifact = await find_artifact(session, job)
            script = await session.get(ScriptVersion, job.source_script_version_id)
            config = dict(job.config)
            try:
                ready = artifact and await asyncio.to_thread(verify, artifact)
                if ready:
                    job.stage = "uploading"
                    event(
                        session,
                        job,
                        "resume_upload",
                        "Recovering saved audio; no new synthesis call",
                    )
                else:
                    # Never silently re-synthesize when an explicit resume checkpoint disappeared.
                    if config.get("resume_artifact_id"):
                        if artifact:
                            artifact.status = "invalid"
                            artifact.last_error = (
                                "Audio checkpoint is missing, expired, or damaged"
                            )
                            await session.commit()
                        raise ProviderError(
                            "AUDIO_CHECKPOINT_INVALID",
                            "Saved audio is unavailable; review before a new synthesis call",
                            ambiguous=True,
                        )
                    approval = await current_approval(session, script)
                    if str(approval.id) != config["approval_id"]:
                        raise ApiError(
                            409,
                            "APPROVAL_STALE",
                            "The approval changed after this audio request",
                        )
                    active = provider or get_tts_provider(
                        execution_settings(job.provider_snapshot)
                    )
                    if (
                        active.name != config["provider"]
                        or active.model != config["model"]
                    ):
                        raise ProviderError(
                            "TTS_CONFIG_CHANGED", "Fix TTS configuration and retry"
                        )
                    call_id = uuid.uuid4()
                    session.add(
                        ProviderCall(
                            id=call_id,
                            job_id=job_id,
                            execution_id=execution_id,
                            operation="audio_generation",
                            provider=active.name,
                            requested_model=active.model,
                            input_characters=len(script.text),
                            status="running",
                        )
                    )
                    await session.flush()
                    artifact_id, audio_id = uuid.uuid4(), uuid.uuid4()
                    artifact = AudioArtifact(
                        id=artifact_id,
                        job_id=job_id,
                        provider_call_id=call_id,
                        approval_id=approval.id,
                        audio_id=audio_id,
                        staging_path=f"{artifact_id}.mp3",
                        bucket=storage.bucket
                        if storage
                        else get_settings().minio_bucket,
                        object_key=f"audio/{audio_id}.mp3",
                        status="preparing",
                        expires_at=datetime.now(UTC) + timedelta(days=7),
                    )
                    session.add(artifact)
                    job.stage = "provider_call"
                    event(session, job, "provider_call", "Generating MP3 audio")
                await session.commit()
            except (ApiError, ProviderError) as error:
                await session.rollback()
                await record_failure(
                    session_factory,
                    job_id,
                    execution_id,
                    error
                    if isinstance(error, ProviderError)
                    else ProviderError(error.code, error.message),
                )
                return
        synthesis_result = None
        try:
            if not ready:
                path, _ = paths(artifact)
                part = path.with_suffix(".part")
                result = await active.synthesize(
                    script.text,
                    config["voice_id"],
                    config["language"],
                    config["speed"],
                    job_id,
                    part,
                )
                synthesis_result = result
                metadata = await asyncio.to_thread(
                    checkpoint, artifact, part, execution_id, result, active.model
                )
                async with session_factory() as session:
                    job = await fenced(session, job_id, execution_id)
                    if not job:
                        return
                    call = await session.get(ProviderCall, call_id)
                    apply_call_metadata(call, metadata)
                    call.audio_duration_seconds = metadata["duration_seconds"]
                    call.status, call.completed_at = "succeeded", datetime.now(UTC)
                    saved = await session.get(AudioArtifact, artifact.id)
                    saved.status = "ready"
                    saved.sha256, saved.file_size, saved.duration_seconds = (
                        metadata["sha256"],
                        metadata["file_size"],
                        metadata["duration_seconds"],
                    )
                    job.stage = "uploading"
                    event(
                        session,
                        job,
                        "progress",
                        "Audio checkpoint saved; uploading to private storage",
                    )
                    await session.commit()
            await upload_artifact(
                job_id, execution_id, artifact, session_factory, storage
            )
        except Exception as exc:  # noqa: BLE001 - persist sanitized worker failure state
            if isinstance(exc, ProviderError):
                error = exc
            else:
                error = ProviderError(
                    "AUDIO_SAVE_FAILED",
                    "Audio upload or saving failed; saved checkpoints can be recovered without another synthesis call",
                    ambiguous=True,
                )
            if synthesis_result is not None:
                error.metadata.update(vars(synthesis_result))
                error.metadata["actual_model"] = active.model
            await record_failure(session_factory, job_id, execution_id, error, call_id)


@celery_app.task(name="app.audio_worker.generate_audio_task")
def generate_audio_task(job_id):
    run_task(job_id, process_audio_job)
