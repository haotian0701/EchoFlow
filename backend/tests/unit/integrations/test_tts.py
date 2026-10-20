import uuid

import httpx
import pytest

from app.api.audio import byte_range
from app.core.config import Settings
from app.core.provider_errors import ProviderError
from app.integrations.tts import ElevenLabsProvider, inspect_mp3
from tests.factories import CHIME

BODY = {"language": "en", "voice_id": "mock-chime", "speed": 1}


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


@pytest.mark.parametrize(
    "header", ["bytes=-0", "bytes=-", "bytes=8-3", "bytes=0-1,5-6", "invalid"]
)
def test_invalid_ranges(header):
    with pytest.raises(Exception) as caught:
        byte_range(header, 100)
    assert caught.value.status == 416
