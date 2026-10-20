import json
import logging

from app.core.config import Settings
from app.core.logging import SafeJsonFormatter


def test_typed_defaults_env_overrides_and_log_allowlist(monkeypatch):
    settings = Settings(_env_file=None)
    assert (
        settings.openrouter_model
        == settings.openrouter_evaluation_model
        == "z-ai/glm-5.3-flash"
    )


def test_log_and_env_allowlist(monkeypatch):
    monkeypatch.setenv("GENERATION_TOKENS_SHORT", "9000")
    assert Settings(_env_file=None).generation_tokens_short == 9000
    record = logging.LogRecord(
        "app.test", logging.ERROR, "", 0, "heartbeat_failed", (), None
    )
    record.job_id = "job"
    record.api_key = "secret"
    record.prompt = "private article"
    result = json.loads(SafeJsonFormatter().format(record))
    assert (
        result["job_id"] == "job"
        and "secret" not in json.dumps(result)
        and "prompt" not in result
    )
