import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.time import utc
from app.db.session import SessionLocal
from app.integrations.audio_staging import remove, verify
from app.integrations.storage import get_audio_storage
from app.models.audio import AudioArtifact, AudioAsset
from app.models.jobs import (
    ProductionJob,
)
from app.services.execution import lock_job


async def storage_once(session_factory=SessionLocal, storage=None):
    now = datetime.now(UTC)
    store = storage or get_audio_storage()
    async with session_factory() as session:
        ids = (
            await session.scalars(
                select(AudioAsset.id).where(
                    AudioAsset.status.in_(["deleting", "delete_failed"]),
                    AudioAsset.delete_attempts < 3,
                )
            )
        ).all()
    for asset_id in ids:
        async with session_factory() as session:
            asset = await session.get(AudioAsset, asset_id, with_for_update=True)
            if (
                not asset
                or asset.status not in ("deleting", "delete_failed")
                or asset.delete_attempts >= 3
            ):
                continue
            asset.delete_attempts += 1
            try:
                await asyncio.to_thread(store.delete, asset.bucket, asset.object_key)
                asset.status, asset.deleted_at, asset.delete_error = (
                    "deleted",
                    now,
                    None,
                )
            except Exception:  # noqa: BLE001 - isolate service failures without logging sensitive exception payloads
                asset.status, asset.delete_error = (
                    "delete_failed",
                    "Private storage deletion failed; retry from Audio Studio",
                )
            await session.commit()
    async with session_factory() as session:
        artifacts = (await session.scalars(select(AudioArtifact))).all()
        for artifact in artifacts:
            job = await lock_job(session, artifact.job_id)
            # Locking the article/job also excludes new resume jobs created by the retry API.
            active = await session.scalar(
                select(ProductionJob.id).where(
                    ProductionJob.article_id == job.article_id,
                    ProductionJob.status.in_(["queued", "running", "retry_scheduled"]),
                )
            )
            if active:
                continue
            if (
                artifact.status == "preparing"
                and utc(artifact.created_at) < now - timedelta(hours=24)
                and not await asyncio.to_thread(verify, artifact)
            ):
                await asyncio.to_thread(remove, artifact)
                artifact.status = "expired"
            if artifact.status == "completed" or utc(artifact.expires_at) <= now:
                await asyncio.to_thread(remove, artifact)
                if artifact.status != "completed":
                    artifact.status = "expired"
        await session.commit()
    # Only remove unreferenced fragments; complete referenced checkpoints retain their full 7 days.
    root = Path(get_settings().audio_staging_path)
    async with session_factory() as session:
        referenced = {
            str(a.id) for a in (await session.scalars(select(AudioArtifact))).all()
        }
    if root.exists():
        for path in root.iterdir():
            if (
                path.is_file()
                and path.name.split(".")[0] not in referenced
                and path.stat().st_mtime < (now - timedelta(hours=24)).timestamp()
            ):
                path.unlink(missing_ok=True)
    # An object reserved by any live artifact or asset is never an orphan.
    for obj in await asyncio.to_thread(lambda: list(store.list_objects())):
        if not obj.last_modified or utc(obj.last_modified) >= now - timedelta(hours=24):
            continue
        async with session_factory() as session:
            asset = await session.scalar(
                select(AudioAsset.id).where(
                    AudioAsset.object_key == obj.object_name,
                    AudioAsset.status != "deleted",
                )
            )
            artifact = await session.scalar(
                select(AudioArtifact).where(AudioArtifact.object_key == obj.object_name)
            )
            active = False
            if artifact:
                source_job = await lock_job(session, artifact.job_id)
                active = await session.scalar(
                    select(ProductionJob.id).where(
                        ProductionJob.article_id == source_job.article_id,
                        ProductionJob.status.in_(
                            ["queued", "running", "retry_scheduled"]
                        ),
                    )
                )
            protected = artifact and artifact.status not in ("expired", "completed")
            if not asset and not protected and not active:
                await asyncio.to_thread(store.delete, store.bucket, obj.object_name)
