"""Default tests are isolated from local credentials and paid providers."""

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.session import get_session
from app.integrations import tts
from app.main import app
from tests.database import make_database


@pytest.fixture(autouse=True)
def mock_config(monkeypatch, tmp_path):
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        tts_provider="mock",
        openrouter_api_key="",
        elevenlabs_api_key="",
        audio_staging_path=str(tmp_path),
        database_url="sqlite+aiosqlite:///:memory:",
    )
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "get_settings"):
            monkeypatch.setattr(module, "get_settings", lambda: settings)
    tts._voice_cache.clear()
    return settings


@pytest_asyncio.fixture
async def sessions():
    engine, factory = await make_database()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(sessions):
    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
