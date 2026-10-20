"""Black-box tests may write only to the fixed, verified Mock test endpoint."""

import os
import subprocess

import httpx
import pytest

from tests.e2e.support import API_URL, ROOT, ProductionFlow


@pytest.fixture
def live_api():
    if os.getenv("ECHOFLOW_E2E") != "1":
        pytest.skip(
            "Run through scripts/run-e2e.ps1 or explicitly enable the isolated Mock suite"
        )
    with httpx.Client(base_url=API_URL, timeout=20) as client:
        health = client.get("/health")
        health.raise_for_status()
        assert health.json().get("providers") == {"llm": "mock", "tts": "mock"}, (
            "E2E refused: target API must use Mock providers"
        )
        assert client.get("/health/ready").status_code == 200
        yield client


@pytest.fixture
def production(live_api):
    return ProductionFlow(live_api)


@pytest.fixture
def compose(live_api):
    def command(*args):
        files = ["-f", "compose.yaml", "-f", "backend/tests/e2e/compose.yaml"]
        if os.getenv("ECHOFLOW_E2E_RECOVERY") == "1":
            files += ["-f", "backend/tests/e2e/compose.recovery.yaml"]
        result = subprocess.run(
            ["docker", "compose", "-p", "echoflow-e2e", *files, *args],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        return result.stdout

    return command
