"""Durable, self-verifying checkpoint. The JSON rename is the commit marker."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.execution import utc
from app.llm import ProviderError
from app.tts import inspect_mp3


def paths(artifact):
    root = Path(get_settings().audio_staging_path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Never trust a stored path outside the configured mount.
    path = (root / artifact.staging_path).resolve()
    if path.parent != root or path.name != f"{artifact.id}.mp3":
        raise ValueError("Invalid artifact path")
    return path, path.with_suffix(".json")


def durable_write(path, data):
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def checkpoint(artifact, part, execution_id, result, actual_model):
    path, marker = paths(artifact)
    duration, size = inspect_mp3(part)
    with part.open("r+b") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        os.fsync(stream.fileno())
    metadata = {
        "artifact_id": str(artifact.id),
        "job_id": str(artifact.job_id),
        "provider_call_id": str(artifact.provider_call_id),
        "approval_id": str(artifact.approval_id),
        "audio_id": str(artifact.audio_id),
        "execution_id": str(execution_id),
        "object_key": artifact.object_key,
        "sha256": digest,
        "file_size": size,
        "duration_seconds": duration,
        "provider_request_id": result.provider_request_id,
        "provider_credits": result.provider_credits,
        "latency_ms": result.latency_ms,
        "actual_model": actual_model,
    }
    os.replace(part, path)
    temporary = marker.with_suffix(".json.part")
    durable_write(temporary, json.dumps(metadata, sort_keys=True).encode())
    os.replace(temporary, marker)
    sync_directory(path.parent)
    return metadata


def verify(artifact, *, allow_expired=False):
    if not allow_expired and utc(artifact.expires_at) <= datetime.now(UTC):
        return None
    try:
        path, marker = paths(artifact)
        data = json.loads(marker.read_text(encoding="utf-8"))
        expected = {
            "artifact_id": str(artifact.id),
            "job_id": str(artifact.job_id),
            "provider_call_id": str(artifact.provider_call_id),
            "approval_id": str(artifact.approval_id),
            "audio_id": str(artifact.audio_id),
            "object_key": artifact.object_key,
        }
        if any(data.get(k) != v for k, v in expected.items()):
            return None
        if artifact.sha256 and data.get("sha256") != artifact.sha256:
            return None
        duration, size = inspect_mp3(path)
        if size != data["file_size"] or duration != data["duration_seconds"]:
            return None
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != data["sha256"]:
                return None
        return data
    except (OSError, ValueError, KeyError, TypeError, ProviderError):
        return None


def remove(artifact):
    path, marker = paths(artifact)
    for target in (
        path,
        marker,
        path.with_suffix(".part"),
        marker.with_suffix(".json.part"),
    ):
        target.unlink(missing_ok=True)
