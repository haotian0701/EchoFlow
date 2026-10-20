from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.audio import AudioAsset
from app.services.audio_production import process_audio_job
from app.services.storage_cleanup import storage_once
from tests.factories import approved_script, queued_job
from tests.fakes import MemoryStorage


@pytest.mark.asyncio
async def test_soft_delete_automatic_retry_cap_and_orphan_protection(sessions, client):
    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    store = MemoryStorage()
    store.list_objects = list
    await process_audio_job(job_id, session_factory=sessions, storage=store)
    async with sessions() as session:
        asset = await session.scalar(select(AudioAsset))
        asset.status = "deleting"
        store.fail_delete = True
        await session.commit()
    for _ in range(4):
        await storage_once(sessions, store)
    async with sessions() as session:
        asset = await session.get(AudioAsset, asset.id)
        assert asset.delete_attempts == 3 and asset.status == "delete_failed"
        asset.delete_attempts = 0
        store.fail_delete = False
        await session.commit()
    await storage_once(sessions, store)
    async with sessions() as session:
        assert (await session.get(AudioAsset, asset.id)).status == "deleted"


@pytest.mark.asyncio
async def test_orphan_cleanup_preserves_referenced_and_active_objects(
    sessions, client, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    _, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    store = MemoryStorage()
    await process_audio_job(job_id, session_factory=sessions, storage=store)
    known_key = next(iter(store.objects))[1]
    old = datetime.now(UTC) - timedelta(days=2)
    store.objects[store.bucket, "audio/orphan.mp3"] = b"orphan"
    store.list_objects = lambda: [
        SimpleNamespace(object_name=key, last_modified=old) for _, key in store.objects
    ]
    fragment = tmp_path / "unreferenced.part"
    fragment.write_bytes(b"fragment")
    import os

    os.utime(fragment, (old.timestamp(), old.timestamp()))
    await storage_once(sessions, store)
    assert (store.bucket, known_key) in store.objects
    assert (
        store.bucket,
        "audio/orphan.mp3",
    ) not in store.objects and not fragment.exists()
