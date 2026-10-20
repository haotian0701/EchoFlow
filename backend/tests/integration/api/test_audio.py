import uuid

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.integrations.storage import get_audio_storage
from app.main import app
from app.models.jobs import ProductionJob, ProviderCall
from app.services.audio_production import process_audio_job
from tests.factories import CHIME, approved_script, queued_job, seed_script
from tests.fakes import MemoryStorage

BODY = {"language": "en", "voice_id": "mock-chime", "speed": 1}


@pytest.mark.asyncio
async def test_audio_requires_current_approval_language_voice_and_config(
    sessions, client, monkeypatch
):
    _, script = await seed_script(client, sessions)
    response = await client.post(f"/scripts/{script.id}/audio", json=BODY)
    assert response.status_code == 409
    assert response.json()["code"] == "APPROVAL_STALE"
    assert "request_id" in response.json()
    for override in ({"speed": 1.3}, {"speed": 0.6}, {"voice_id": "../secret"}):
        assert (
            await client.post(f"/scripts/{script.id}/audio", json={**BODY, **override})
        ).status_code == 422
    wrong_language = await client.post(
        f"/scripts/{script.id}/audio", json={**BODY, "language": "de"}
    )
    assert wrong_language.json()["code"] == "TTS_LANGUAGE_MISMATCH"
    wrong_voice = await client.post(
        f"/scripts/{script.id}/audio", json={**BODY, "voice_id": "nonexistent"}
    )
    assert wrong_voice.json()["code"] == "TTS_VOICE_UNAVAILABLE"
    missing = await client.post(f"/scripts/{uuid.uuid4()}/audio", json=BODY)
    assert missing.status_code == 404
    from app.integrations import tts

    monkeypatch.setattr(
        tts,
        "get_settings",
        lambda: Settings(
            _env_file=None, tts_provider="elevenlabs", elevenlabs_api_key=""
        ),
    )
    assert (await client.get("/tts/voices?language=en")).json()[
        "code"
    ] == "TTS_NOT_CONFIGURED"
    assert (
        await client.post(f"/scripts/{script.id}/audio", json=BODY)
    ).status_code == 503


@pytest.mark.asyncio
async def test_audio_idempotency_aliases_and_reuse(sessions, client):
    _, script = await approved_script(client, sessions)
    key, alias = str(uuid.uuid4()), str(uuid.uuid4())
    url = f"/scripts/{script.id}/audio"
    first = await client.post(url, json=BODY, headers={"Idempotency-Key": key})
    assert first.status_code == 202
    assert first.headers["location"] == f"/jobs/{first.json()['job']['id']}"
    for other_key in (key, alias):
        reused = await client.post(
            url, json=BODY, headers={"Idempotency-Key": other_key}
        )
        assert reused.status_code == 200
        assert reused.json()["job"]["id"] == first.json()["job"]["id"]
        conflict = await client.post(
            url, json={**BODY, "speed": 1.1}, headers={"Idempotency-Key": other_key}
        )
        assert conflict.status_code == 409
    jobs = (await client.get("/jobs?type=audio_generation&limit=1")).json()
    assert jobs["total"] == 1
    events = (await client.get(f"/jobs/{first.json()['job']['id']}/events")).json()
    assert events["items"][0]["stage"] == "queued"


@pytest.mark.asyncio
async def test_audio_success_stream_range_download_soft_delete_and_regenerate(
    sessions, client
):
    article, script = await approved_script(client, sessions)
    job_id = await queued_job(client, script)
    storage = MemoryStorage()
    app.dependency_overrides[get_audio_storage] = lambda: storage
    await process_audio_job(job_id, session_factory=sessions, storage=storage)
    await process_audio_job(job_id, session_factory=sessions, storage=storage)
    async with sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == "succeeded"
        audio_id = job.result_audio_asset_id
        assert await session.scalar(select(func.count()).select_from(ProviderCall)) == 1
        call = await session.scalar(select(ProviderCall))
        assert call.operation == "audio_generation" and call.status == "succeeded"
        assert call.input_characters == len(script.text)
    detail = (await client.get(f"/audio/{audio_id}")).json()
    assert detail["article"]["id"] == article["id"] and detail["script_version"] == 1
    assert detail["duration_seconds"] > 1
    assert "object_key" not in detail  # private storage topology stays server-side
    reused = await client.post(f"/scripts/{script.id}/audio", json=BODY)
    assert reused.status_code == 200 and reused.json()["audio"]["id"] == str(audio_id)
    listing = (await client.get("/audio?q=annual&limit=1")).json()
    assert listing["total"] == 1
    content = CHIME.read_bytes()
    full = await client.get(f"/audio/{audio_id}/stream")
    assert full.content == content and full.headers["accept-ranges"] == "bytes"
    for value, expected in (
        ("bytes=0-9", content[:10]),
        ("bytes=-10", content[-10:]),
        ("bytes=10-", content[10:]),
    ):
        response = await client.get(
            f"/audio/{audio_id}/stream", headers={"Range": value}
        )
        assert response.status_code == 206 and response.content == expected
        assert response.headers["content-range"].startswith("bytes ")
    bad = await client.get(
        f"/audio/{audio_id}/stream", headers={"Range": "bytes=9999999-"}
    )
    assert (
        bad.status_code == 416
        and bad.headers["content-range"] == f"bytes */{len(content)}"
    )
    download = await client.get(f"/audio/{audio_id}/download")
    assert (
        download.content == content
        and "filename*=UTF-8''" in download.headers["content-disposition"]
    )
    storage.fail_delete = True
    assert (await client.delete(f"/audio/{audio_id}")).status_code == 503
    assert (await client.get(f"/audio/{audio_id}")).json()["status"] == "delete_failed"
    storage.fail_delete = False
    for _ in range(2):
        assert (await client.delete(f"/audio/{audio_id}")).status_code == 204
    assert not storage.objects
    assert (await client.get("/audio")).json()["total"] == 0
    assert (await client.get("/audio?status=deleted")).json()["total"] == 1
    assert (await client.get(f"/audio/{audio_id}/stream")).status_code == 410
    assert (await client.get(f"/audio/{audio_id}")).json()["job"][
        "status"
    ] == "succeeded"
    await queued_job(client, script)
