import io
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.audio import byte_range
from app.audio_worker import process_audio_job
from app.config import Settings
from app.llm import ProviderError
from app.main import app
from app.models import AudioAsset, Evaluation, ProductionJob, ProviderCall, TaskOutbox
from app.storage import get_audio_storage
from app.tts import ElevenLabsProvider, MockTTSProvider, inspect_mp3
from tests.test_reviews import seed_script

CHIME = Path(__file__).parents[1] / "app/assets/mock-chime.mp3"
BODY = {"language": "en", "voice_id": "mock-chime", "speed": 1}


class MemoryStorage:
    bucket = "test-audio"

    def __init__(self):
        self.objects = {}
        self.upload_attempts = 0
        self.fail_upload = False
        self.fail_delete = False

    def upload(self, bucket, key, path):
        self.upload_attempts += 1
        if self.fail_upload:
            raise OSError("Storage is unavailable")
        self.objects[bucket, key] = path.read_bytes()

    def open(self, bucket, key, offset=0, length=0):
        content = self.objects[bucket, key]
        return io.BytesIO(
            content[offset : offset + length] if length else content[offset:]
        )

    def delete(self, bucket, key):
        if self.fail_delete:
            raise OSError("Storage deletion failed")
        self.objects.pop((bucket, key), None)


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    from app import tts

    monkeypatch.setattr(
        tts, "get_settings", lambda: Settings(_env_file=None, tts_provider="mock")
    )
    tts._voice_cache.clear()


async def approved_script(client):
    article, script = await seed_script(client)
    evaluated = await client.post(f"/scripts/{script.id}/evaluate")
    evaluation_id = uuid.UUID(evaluated.json()["evaluation"]["id"])
    async with client.test_sessions() as session:
        evaluation = await session.get(Evaluation, evaluation_id)
        evaluation.status = "succeeded"
        await session.commit()
    response = await client.post(
        f"/scripts/{script.id}/approve",
        json={
            "evaluation_id": str(evaluation_id),
            "expected_version": 1,
            "acknowledged_critical_issue_ids": [],
        },
    )
    assert response.status_code == 201
    return article, script


async def queued_job(client, script):
    response = await client.post(
        f"/scripts/{script.id}/audio",
        json=BODY,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["job"]["id"])


@pytest.mark.asyncio
async def test_audio_requires_current_approval_language_voice_and_config(
    client, monkeypatch
):
    _, script = await seed_script(client)
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
    from app import tts

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
async def test_audio_idempotency_aliases_and_reuse(client):
    _, script = await approved_script(client)
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
async def test_audio_success_stream_range_download_soft_delete_and_regenerate(client):
    article, script = await approved_script(client)
    job_id = await queued_job(client, script)
    storage = MemoryStorage()
    app.dependency_overrides[get_audio_storage] = lambda: storage
    await process_audio_job(
        job_id, session_factory=client.test_sessions, storage=storage
    )
    await process_audio_job(
        job_id, session_factory=client.test_sessions, storage=storage
    )
    async with client.test_sessions() as session:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["evaluation", "version"])
async def test_worker_rechecks_approval_before_paid_call(client, change):
    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    if change == "evaluation":
        await client.post(f"/scripts/{script.id}/evaluate")
    else:
        await client.patch(
            f"/scripts/{script.id}",
            json={"text": script.text + " Updated.", "expected_version": 1},
        )
    await process_audio_job(
        job_id, session_factory=client.test_sessions, storage=MemoryStorage()
    )
    async with client.test_sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == "failed" and job.error_code == "APPROVAL_STALE"
        assert await session.scalar(select(func.count()).select_from(ProviderCall)) == 0


class FailingTTS(MockTTSProvider):
    def __init__(self, retryable=False, ambiguous=False):
        self.retryable, self.ambiguous = retryable, ambiguous

    async def synthesize(self, *args):
        raise ProviderError(
            "TTS_TEST_ERROR",
            "Test provider failure",
            retryable=self.retryable,
            ambiguous=self.ambiguous,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retryable,ambiguous,expected",
    [
        (True, False, "retry_scheduled"),
        (False, True, "needs_attention"),
        (False, False, "failed"),
    ],
)
async def test_worker_failure_policy(client, retryable, ambiguous, expected):
    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    await process_audio_job(
        job_id,
        session_factory=client.test_sessions,
        provider=FailingTTS(retryable, ambiguous),
        storage=MemoryStorage(),
    )
    async with client.test_sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == expected and job.attempt_count == 1
        outbox = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        assert outbox.queue_name == "audio_generation"


@pytest.mark.asyncio
async def test_upload_failure_does_not_repeat_or_erase_successful_provider_call(
    client, monkeypatch
):
    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    storage = MemoryStorage()
    storage.fail_upload = True

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr("app.audio_worker.asyncio.sleep", no_sleep)
    await process_audio_job(
        job_id, session_factory=client.test_sessions, storage=storage
    )
    assert storage.upload_attempts == 3
    async with client.test_sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == "needs_attention"
        call = await session.scalar(select(ProviderCall))
        assert call.status == "succeeded"
        assert await session.scalar(select(func.count()).select_from(AudioAsset)) == 0


@pytest.mark.asyncio
async def test_elevenlabs_synthesizes_valid_mp3_and_usage(tmp_path):
    def handler(request):
        import json

        body = json.loads(request.content)
        assert request.headers["xi-api-key"] == "test-key"
        assert request.url.params["output_format"] == "mp3_44100_128"
        assert body["language_code"] == "de" and body["voice_settings"]["speed"] == 0.9
        return httpx.Response(
            200,
            content=CHIME.read_bytes(),
            headers={"request-id": "tts-123", "character-cost": "123.5"},
        )

    provider = ElevenLabsProvider(
        Settings(_env_file=None, elevenlabs_api_key="test-key"),
        httpx.MockTransport(handler),
    )
    path = tmp_path / "audio.mp3"
    result = await provider.synthesize(
        "Bericht aus Berlin. " * 10, "voice", "de", 0.9, uuid.uuid4(), path
    )
    assert result.provider_request_id == "tts-123" and result.provider_credits == 123.5
    assert inspect_mp3(path)[0] > 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,retryable",
    [
        (401, False),
        (402, False),
        (403, False),
        (404, False),
        (422, False),
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
    ],
)
async def test_elevenlabs_error_classification(tmp_path, status, retryable):
    provider = ElevenLabsProvider(
        Settings(_env_file=None),
        httpx.MockTransport(
            lambda _: httpx.Response(
                status, json={"detail": {"message": "secret echoed text"}}
            )
        ),
    )
    with pytest.raises(ProviderError) as caught:
        await provider.synthesize(
            "word " * 25, "voice", "en", 1, uuid.uuid4(), tmp_path / "audio.mp3"
        )
    assert caught.value.retryable == retryable
    assert "secret" not in caught.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,retryable,ambiguous",
    [
        (httpx.ConnectError("offline"), True, False),
        (httpx.ReadTimeout("unknown"), False, True),
    ],
)
async def test_elevenlabs_transport_errors(tmp_path, error, retryable, ambiguous):
    def handler(_request):
        raise error

    provider = ElevenLabsProvider(
        Settings(_env_file=None), httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderError) as caught:
        await provider.synthesize(
            "word " * 25, "voice", "en", 1, uuid.uuid4(), tmp_path / "audio.mp3"
        )
    assert caught.value.retryable == retryable and caught.value.ambiguous == ambiguous


@pytest.mark.asyncio
async def test_elevenlabs_size_limit_invalid_mp3_and_voice_pagination(tmp_path):
    provider = ElevenLabsProvider(
        Settings(_env_file=None, tts_max_response_bytes=10),
        httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 11)),
    )
    with pytest.raises(ProviderError, match="size limit"):
        await provider.synthesize(
            "word " * 25, "voice", "en", 1, uuid.uuid4(), tmp_path / "audio.mp3"
        )
    path = tmp_path / "invalid.mp3"
    path.write_bytes(b"this is not mp3")
    with pytest.raises(ProviderError, match="invalid MP3"):
        inspect_mp3(path)

    def handler(request):
        second = request.url.params.get("next_page_token") == "page2"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "voice_id": "v2" if second else "v1",
                        "name": "Voice",
                        "verified_languages": [{"language": "de"}],
                    }
                ],
                "has_more": not second,
                "next_page_token": "page2" if not second else None,
            },
        )

    provider = ElevenLabsProvider(
        Settings(_env_file=None), httpx.MockTransport(handler)
    )
    voices = await provider.list_voices("de")
    assert {voice["id"] for voice in voices} == {"v1", "v2"}
    assert voices[0]["supported_languages"] == ["en", "de"]


@pytest.mark.asyncio
async def test_invalid_provider_audio_fails_without_creating_asset(client):
    from app.tts import SynthesisResult

    class InvalidAudio(MockTTSProvider):
        async def synthesize(self, text, voice, language, speed, job_id, output_path):
            output_path.write_bytes(b"not an MP3")
            return SynthesisResult()

    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    await process_audio_job(
        job_id,
        session_factory=client.test_sessions,
        provider=InvalidAudio(),
        storage=MemoryStorage(),
    )
    async with client.test_sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == "failed" and job.error_code == "INVALID_TTS_AUDIO"
        assert await session.scalar(select(func.count()).select_from(AudioAsset)) == 0


@pytest.mark.asyncio
async def test_retry_exhaustion_and_new_request_after_failure(client):
    _, script = await approved_script(client)
    job_id = await queued_job(client, script)
    for attempt in range(3):
        await process_audio_job(
            job_id,
            session_factory=client.test_sessions,
            provider=FailingTTS(retryable=True),
            storage=MemoryStorage(),
        )
        async with client.test_sessions() as session:
            job = await session.get(ProductionJob, job_id)
            assert job.attempt_count == attempt + 1
            if attempt < 2:
                assert job.status == "retry_scheduled"
                job.status = "queued"
                await session.commit()
            else:
                assert job.status == "failed"
    new_job = await queued_job(client, script)
    assert new_job != job_id


@pytest.mark.parametrize(
    "header", ["bytes=-0", "bytes=-", "bytes=8-3", "bytes=0-1,5-6", "invalid"]
)
def test_invalid_ranges(header):
    with pytest.raises(Exception) as caught:
        byte_range(header, 100)
    assert caught.value.status == 416
