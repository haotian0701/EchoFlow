import asyncio
import logging
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.audio_service import current_approval
from app.celery_app import celery_app
from app.config import get_settings
from app.database import SessionLocal
from app.errors import ApiError
from app.llm import ProviderError
from app.models import AudioAsset, JobEvent, ProductionJob, ProviderCall, ScriptVersion
from app.storage import get_audio_storage
from app.tts import get_tts_provider, inspect_mp3
from app.worker import _record_failure


async def process_audio_job(
    job_id, *, session_factory=SessionLocal, provider=None, storage=None
):
    call_id = uuid.uuid4()
    async with session_factory() as session:
        job = await session.get(ProductionJob, job_id, with_for_update=True)
        if (
            not job
            or job.job_type != "audio_generation"
            or job.status != "queued"
            or job.result_audio_asset_id
        ):
            return
        job.attempt_count += 1
        job.started_at = job.started_at or datetime.now(UTC)
        script = await session.get(ScriptVersion, job.source_script_version_id)
        try:
            if not script:
                raise ApiError(
                    409, "APPROVAL_STALE", "The source script is unavailable"
                )
            approval = await current_approval(session, script)
            if str(approval.id) != job.config["approval_id"]:
                raise ApiError(
                    409,
                    "APPROVAL_STALE",
                    "The approval changed after this audio request",
                )
            active_provider = provider or get_tts_provider()
            if (
                active_provider.name != job.config["provider"]
                or active_provider.model != job.config["model"]
            ):
                raise ProviderError(
                    "TTS_CONFIG_CHANGED",
                    "TTS configuration changed after this request; create a new audio job",
                )
        except (ApiError, ProviderError) as exc:
            job.status = job.stage = "failed"
            job.error_code, job.error_message = exc.code, exc.message
            job.completed_at = datetime.now(UTC)
            session.add(
                JobEvent(
                    job_id=job.id,
                    event_type="failed",
                    stage="failed",
                    message=exc.message,
                )
            )
            await session.commit()
            return
        job.status, job.stage = "running", "provider_call"
        job.error_code = job.error_message = None
        session.add(
            JobEvent(
                job_id=job.id,
                event_type="validated",
                stage="validating_approval",
                message="Approved script version verified",
            )
        )
        session.add(
            JobEvent(
                job_id=job.id,
                event_type="started",
                stage="provider_call",
                message="Generating MP3 audio",
            )
        )
        session.add(
            ProviderCall(
                id=call_id,
                job_id=job.id,
                operation="audio_generation",
                provider=active_provider.name,
                requested_model=active_provider.model,
                input_characters=len(script.text),
                status="running",
            )
        )
        config = dict(job.config)
        article_id = job.article_id
        await session.commit()
    audio_id = uuid.uuid4()
    uploaded = False
    try:
        store = storage or get_audio_storage()
        bucket, object_key = store.bucket, f"audio/{audio_id}.mp3"
        with tempfile.TemporaryDirectory(prefix="echoflow-tts-") as folder:
            path = Path(folder) / "audio.mp3"
            result = await active_provider.synthesize(
                script.text,
                config["voice_id"],
                config["language"],
                config["speed"],
                job_id,
                path,
            )
            duration, size = inspect_mp3(path)
            # Persist successful provider accounting before storage; upload failures
            # must not erase a billed TTS call or cause synthesis to repeat.
            async with session_factory() as session:
                call = await session.get(ProviderCall, call_id)
                call.status = "succeeded"
                call.actual_model = active_provider.model
                call.provider_request_id = result.provider_request_id
                call.provider_credits = result.provider_credits
                call.latency_ms = result.latency_ms
                call.audio_duration_seconds = duration
                call.completed_at = datetime.now(UTC)
                job = await session.get(ProductionJob, job_id)
                job.stage = "uploading"
                session.add(
                    JobEvent(
                        job_id=job_id,
                        event_type="progress",
                        stage="uploading",
                        message="Uploading audio to private storage",
                    )
                )
                await session.commit()
            for attempt in range(3):
                try:
                    await asyncio.to_thread(store.upload, bucket, object_key, path)
                    uploaded = True
                    break
                except Exception as exc:
                    if attempt == 2:
                        raise ProviderError(
                            "AUDIO_STORAGE_FAILED",
                            "Speech was generated but could not be saved; generating again may incur another charge",
                            ambiguous=True,
                        ) from exc
                    await asyncio.sleep(1 if attempt == 0 else 3)
            async with session_factory() as session:
                job = await session.get(ProductionJob, job_id, with_for_update=True)
                session.add(
                    JobEvent(
                        job_id=job_id,
                        event_type="progress",
                        stage="saving",
                        message="Saving audio metadata and approval reference",
                    )
                )
                asset = AudioAsset(
                    id=audio_id,
                    article_id=article_id,
                    script_version_id=script.id,
                    approval_id=uuid.UUID(config["approval_id"]),
                    job_id=job_id,
                    provider=config["provider"],
                    model=config["model"],
                    language=config["language"],
                    voice_id=config["voice_id"],
                    voice_name=config["voice_name"],
                    speed=config["speed"],
                    format=config["format"],
                    bucket=bucket,
                    object_key=object_key,
                    duration_seconds=duration,
                    file_size=size,
                    input_characters=len(script.text),
                )
                session.add(asset)
                await session.flush()
                job.result_audio_asset_id = asset.id
                job.status, job.stage = "succeeded", "completed"
                job.completed_at = datetime.now(UTC)
                session.add(
                    JobEvent(
                        job_id=job_id,
                        event_type="succeeded",
                        stage="completed",
                        message="MP3 audio is ready to play and download",
                    )
                )
                await session.commit()
    except Exception as exc:  # noqa: BLE001 - persist every worker failure, never re-run billed synthesis blindly
        if uploaded:
            try:
                await asyncio.to_thread(store.delete, bucket, object_key)
            except Exception:  # noqa: BLE001 - cleanup is best effort after a database failure
                logging.getLogger(__name__).error(
                    "Audio orphan cleanup failed for audio_id=%s", audio_id
                )
        error = (
            exc
            if isinstance(exc, ProviderError)
            else ProviderError(
                "AUDIO_SAVE_FAILED",
                "Audio processing could not complete; generating again may incur another charge",
                ambiguous=True,
            )
        )
        # A storage/DB failure must preserve the successful ProviderCall.
        async with session_factory() as session:
            call = await session.get(ProviderCall, call_id)
            provider_succeeded = call is not None and call.status == "succeeded"
        if not provider_succeeded:
            await _record_failure(session_factory, job_id, call_id, error)
        else:
            async with session_factory() as session:
                job = await session.get(ProductionJob, job_id)
                job.status = job.stage = "needs_attention"
                job.error_code, job.error_message = error.code, error.message
                job.completed_at = datetime.now(UTC)
                session.add(
                    JobEvent(
                        job_id=job_id,
                        event_type="attention_required",
                        stage="needs_attention",
                        message=error.message,
                    )
                )
                await session.commit()


@celery_app.task(name="app.audio_worker.generate_audio_task")
def generate_audio_task(job_id: str):
    async def run():
        engine = create_async_engine(get_settings().database_url)
        try:
            await process_audio_job(
                uuid.UUID(job_id),
                session_factory=async_sessionmaker(engine, expire_on_commit=False),
            )
        finally:
            await engine.dispose()

    asyncio.run(run())
