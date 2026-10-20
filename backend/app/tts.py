"""Server-side TTS adapters. No text, credentials, or audio is logged."""

import asyncio
import hashlib
import math
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

import httpx
from mutagen.mp3 import MP3, HeaderNotFoundError

from app.config import Settings, get_settings
from app.llm import ProviderError

FORMAT = "mp3_44100_128"


@dataclass
class SynthesisResult:
    provider_request_id: str | None = None
    provider_credits: float | None = None
    latency_ms: int | None = None


class TTSProvider(Protocol):
    name: str
    model: str

    async def list_voices(self, language: str) -> list[dict]: ...

    async def synthesize(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float,
        job_id: UUID,
        output_path: Path,
    ) -> SynthesisResult: ...


def inspect_mp3(path: Path) -> tuple[float, int]:
    try:
        audio = MP3(path)
        if audio.info.length <= 0 or audio.info.sample_rate != 44100:
            raise ValueError("Invalid MP3 duration or sample rate")
        return round(audio.info.length, 3), path.stat().st_size
    except (HeaderNotFoundError, ValueError, OSError) as exc:
        raise ProviderError(
            "INVALID_TTS_AUDIO", "TTS returned an invalid MP3 file"
        ) from exc


class MockTTSProvider:
    name = "mock"
    model = "mock/chime-v1"

    async def list_voices(self, language: str) -> list[dict]:
        return [
            {
                "id": "mock-chime",
                "name": "Mock demonstration chime",
                "provider": self.name,
                "supported_languages": ["en", "de"],
                "verified_languages": [],
                "labels": {"type": "test tone, not narration"},
                "preview_url": None,
            }
        ]

    async def synthesize(self, text, voice, language, speed, job_id, output_path):
        shutil.copyfile(
            Path(__file__).parent / "assets" / "mock-chime.mp3", output_path
        )
        return SynthesisResult(provider_request_id=f"mock-{job_id}", latency_ms=0)


def check_response(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    # Allowlisted public messages; never persist upstream bodies or echoed text.
    messages = {
        401: "ElevenLabs API key is invalid",
        402: "ElevenLabs has insufficient credits",
        403: "ElevenLabs denied access to this voice or model",
        404: "The selected voice is unavailable",
        422: "ElevenLabs rejected the voice or synthesis parameters",
    }
    raise ProviderError(
        "TTS_PROVIDER_REJECTED",
        messages.get(response.status_code, "ElevenLabs could not process the request"),
        retryable=response.status_code in {429, 500, 502, 503, 504},
    )


class ElevenLabsProvider:
    name = "elevenlabs"

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.settings = settings
        self.model = settings.elevenlabs_model
        self.transport = transport

    def client(self):
        return httpx.AsyncClient(
            base_url=self.settings.elevenlabs_base_url,
            headers={"xi-api-key": self.settings.elevenlabs_api_key},
            timeout=self.settings.tts_timeout_seconds,
            transport=self.transport,
        )

    async def list_voices(self, language: str) -> list[dict]:
        voices = {}
        params = {"page_size": 100, "include_total_count": "false"}
        try:
            async with self.client() as client:
                async with asyncio.timeout(30):
                    for _ in range(100):
                        response = await client.get("/v2/voices", params=params)
                        check_response(response)
                        data = response.json()
                        for voice in data["voices"]:
                            preview = voice.get("preview_url")
                            voices[voice["voice_id"]] = {
                                "id": voice["voice_id"],
                                "name": voice.get("name") or voice["voice_id"],
                                "provider": self.name,
                                "supported_languages": ["en", "de"],
                                "verified_languages": sorted(
                                    {
                                        item["language"]
                                        for item in (
                                            voice.get("verified_languages") or []
                                        )
                                    }
                                ),
                                "labels": voice.get("labels") or {},
                                "preview_url": preview
                                if preview and preview.startswith("https://")
                                else None,
                            }
                        if not data.get("has_more"):
                            return sorted(
                                voices.values(), key=lambda voice: voice["name"].lower()
                            )
                        token = data.get("next_page_token")
                        if not token or token == params.get("next_page_token"):
                            raise ValueError("Invalid voice pagination")
                        params["next_page_token"] = token
        except ProviderError:
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ProviderError(
                "TTS_VOICES_UNAVAILABLE", "Could not load ElevenLabs voices; try again"
            ) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(
                "TTS_VOICES_INVALID", "ElevenLabs returned an invalid voice list"
            ) from exc
        raise ProviderError(
            "TTS_VOICES_INVALID", "ElevenLabs voice pagination exceeded its limit"
        )

    async def synthesize(self, text, voice, language, speed, job_id, output_path):
        started = time.monotonic()
        self.call_metadata = {}
        try:
            return await self._synthesize(
                text, voice, language, speed, job_id, output_path
            )
        except ProviderError as error:
            error.metadata.update(self.call_metadata)
            error.metadata["latency_ms"] = round((time.monotonic() - started) * 1000)
            raise

    async def _synthesize(self, text, voice, language, speed, job_id, output_path):
        # This adapter supports one explicitly verified model and its 40k limit.
        if self.model != "eleven_flash_v2_5":
            raise ProviderError(
                "TTS_MODEL_UNSUPPORTED", "Stage 4 supports eleven_flash_v2_5 only"
            )
        if not 100 <= len(text) <= 30000:
            raise ProviderError(
                "TTS_TEXT_LIMIT", "The script must contain 100–30,000 characters"
            )
        started = time.monotonic()
        try:
            async with self.client() as client:
                async with asyncio.timeout(self.settings.tts_timeout_seconds):
                    async with client.stream(
                        "POST",
                        f"/v1/text-to-speech/{voice}",
                        params={"output_format": FORMAT},
                        json={
                            "text": text,
                            "model_id": self.model,
                            "language_code": language,
                            "voice_settings": {"speed": speed},
                        },
                    ) as response:
                        self.call_metadata = {
                            "provider_request_id": response.headers.get("request-id"),
                            "actual_model": self.model,
                        }
                        try:
                            credits = float(response.headers["character-cost"])
                            if math.isfinite(credits) and credits >= 0:
                                self.call_metadata["provider_credits"] = credits
                        except (KeyError, ValueError):
                            pass
                        check_response(response)
                        size = 0
                        with output_path.open("wb") as audio:
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > self.settings.tts_max_response_bytes:
                                    raise ProviderError(
                                        "TTS_AUDIO_TOO_LARGE",
                                        "TTS audio exceeds the configured size limit",
                                    )
                                audio.write(chunk)
                        try:
                            credits = float(response.headers["character-cost"])
                            if not math.isfinite(credits) or credits < 0:
                                credits = None
                        except (KeyError, ValueError):
                            credits = None
                        return SynthesisResult(
                            response.headers.get("request-id"),
                            credits,
                            round((time.monotonic() - started) * 1000),
                        )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderError(
                "TTS_UNAVAILABLE", "Could not connect to ElevenLabs", retryable=True
            ) from exc
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ProviderError(
                "TTS_RESULT_UNKNOWN",
                "ElevenLabs did not return a complete result; generating again may incur another charge",
                ambiguous=True,
            ) from exc


def get_tts_provider(settings=None) -> TTSProvider:
    settings = settings or get_settings()
    if settings.tts_provider == "mock":
        return MockTTSProvider()
    if settings.tts_provider == "elevenlabs" and settings.elevenlabs_api_key:
        if settings.elevenlabs_model != "eleven_flash_v2_5":
            raise ProviderError(
                "TTS_NOT_CONFIGURED", "Stage 4 supports eleven_flash_v2_5 only"
            )
        return ElevenLabsProvider(settings)
    raise ProviderError(
        "TTS_NOT_CONFIGURED", "Configure TTS_PROVIDER and the ElevenLabs API key"
    )


_voice_cache: dict[tuple, tuple[float, list[dict]]] = {}


async def cached_voices(language: str) -> tuple[list[dict], bool]:
    provider = get_tts_provider()
    settings = get_settings()
    key = (
        provider.name,
        provider.model,
        language,
        settings.elevenlabs_base_url,
        hashlib.sha256(settings.elevenlabs_api_key.encode()).hexdigest(),
    )
    cached = _voice_cache.get(key)
    if cached and time.monotonic() < cached[0]:
        return cached[1], True
    voices = await provider.list_voices(language)
    # Bound memory when configuration or account changes in a long-lived process.
    if len(_voice_cache) > 16:
        _voice_cache.clear()
    _voice_cache[key] = (time.monotonic() + settings.tts_voice_cache_seconds, voices)
    return voices, False
