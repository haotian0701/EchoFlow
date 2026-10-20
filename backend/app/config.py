from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+asyncpg://echoflow:local-dev-password@localhost:5432/echoflow"
    )
    cors_origin: str = "http://localhost:5173,http://127.0.0.1:5173"
    celery_broker_url: str = "redis://localhost:6379/0"
    llm_provider: str = "mock"
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_evaluation_model: str = "google/gemini-2.5-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = 90.0
    openrouter_http_referer: str = "http://localhost:5173"
    openrouter_app_title: str = "EchoFlow"
    tts_provider: str = "mock"
    elevenlabs_api_key: str = ""
    elevenlabs_model: str = "eleven_flash_v2_5"
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    tts_timeout_seconds: float = 300.0
    tts_max_response_bytes: int = 64 * 1024 * 1024
    tts_voice_cache_seconds: int = 300
    minio_endpoint: str = "http://minio:9000"
    minio_access_key: str = "echoflow"
    minio_secret_key: str = "local-dev-password"
    minio_bucket: str = "echoflow-audio"
    minio_secure: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
