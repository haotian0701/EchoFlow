"""In-memory dependencies shared across test modules."""

import io

from app.core.provider_errors import ProviderError
from app.evaluation.contracts import EvaluationResult
from app.integrations.llm.generation import GenerationResult
from app.integrations.tts import MockTTSProvider


class SuccessfulProvider:
    name = "test"
    requested_model = "test/model"

    async def generate_script(self, article, config, job_id):
        return GenerationResult(
            text=(
                "Welcome to the report. The source states that revenue grew by 12 percent in Berlin. "
                * 3
            ).strip(),
            provider=self.name,
            requested_model=self.requested_model,
            actual_model="test/model-v1",
            provider_request_id=f"test-{job_id}",
            total_tokens=42,
            latency_ms=5,
        )


class FailingProvider:
    name = "test"
    requested_model = "test/model"

    def __init__(self, *, retryable=False, ambiguous=False):
        self.retryable = retryable
        self.ambiguous = ambiguous

    async def generate_script(self, article, config, job_id):
        raise ProviderError(
            "TEST_PROVIDER_ERROR",
            "Provider failed",
            retryable=self.retryable,
            ambiguous=self.ambiguous,
        )


class SuccessfulJudge:
    name = "test"
    requested_model = "test/judge"

    async def evaluate_script(self, article, script, rule_issues, job_id):
        return EvaluationResult(
            factual_consistency=0.9,
            content_coverage=0.8,
            audio_readiness=0.7,
            issues=[],
            provider=self.name,
            requested_model=self.requested_model,
            actual_model="test/judge-v2",
            provider_request_id=f"judge-{job_id}",
            total_tokens=55,
            latency_ms=4,
        )


class FailingJudge:
    name = "test"
    requested_model = "test/judge"

    def __init__(self, *, retryable=False, ambiguous=False):
        self.retryable = retryable
        self.ambiguous = ambiguous

    async def evaluate_script(self, article, script, rule_issues, job_id):
        raise ProviderError(
            "JUDGE_FAILED",
            "Judge failed",
            retryable=self.retryable,
            ambiguous=self.ambiguous,
        )


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
