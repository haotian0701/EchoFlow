import uuid

from sqlalchemy import select

from app.models.audio import AudioArtifact


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
