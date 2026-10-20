"""HTTP-only helpers shared by the opt-in Compose suite."""

import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API_URL = "http://localhost:18000"
WEB_URL = "http://localhost:15173"


class ProductionFlow:
    def __init__(self, client):
        self.client = client

    def post(self, path, body=None, key=None):
        result = self.client.post(
            path, json=body, headers={"Idempotency-Key": key or str(uuid.uuid4())}
        )
        result.raise_for_status()
        return result.json()

    def wait(self, job_id, predicate=None, timeout=110):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/jobs/{job_id}")
            response.raise_for_status()
            job = response.json()
            matched = predicate(job) if predicate else job["status"] == "succeeded"
            if matched:
                return job
            if not predicate and job["status"] in ("failed", "needs_attention"):
                raise AssertionError(
                    f"Job {job_id}: {job['status']} / {job['error_code']}"
                )
            time.sleep(0.3)
        raise AssertionError(
            f"Job {job_id} did not reach the expected state: {job['status']} / {job['stage']}"
        )

    def generate(self, source="e2e-workflow", language="en"):
        sentence = (
            "Researchers in Berlin published a report in 2026. The study involved 120 participants. They announced a public meeting about the findings. "
            if language == "en"
            else "Das Forschungsteam in Berlin hat 2026 einen Bericht veröffentlicht. An der Studie nahmen 120 Personen teil. Eine öffentliche Veranstaltung wurde angekündigt. "
        )
        article = self.post(
            "/articles",
            {
                "title": source + " " + uuid.uuid4().hex[:8],
                "source": source,
                "language": language,
                "text": uuid.uuid4().hex + " " + sentence * 12,
            },
        )["article"]
        body = {
            "type": "script_generation",
            "article_id": article["id"],
            "config": {
                "target_language": language,
                "length": "short",
                "style": "news",
                "audience": "general",
            },
        }
        key = str(uuid.uuid4())
        job = self.post("/jobs", body, key)["job"]
        replay = self.client.post("/jobs", json=body, headers={"Idempotency-Key": key})
        assert replay.status_code == 200 and replay.json()["job"]["id"] == job["id"]
        return article, job

    def approved(self, source="e2e-workflow", language="en"):
        article, job = self.generate(source, language)
        script_id = self.wait(job["id"])["result_script_version_id"]
        evaluation = self.post(f"/scripts/{script_id}/evaluate")
        report_id = self.wait(evaluation["job"]["id"])["result_evaluation_id"]
        report = self.client.get(f"/evaluations/{report_id}").json()
        script = self.client.get(f"/scripts/{script_id}").json()
        self.post(
            f"/scripts/{script_id}/approve",
            {
                "evaluation_id": report_id,
                "expected_version": script["version"],
                "acknowledged_critical_issue_ids": [
                    issue["id"]
                    for issue in report["issues"]
                    if issue["severity"] == "critical"
                ],
            },
        )
        return article, script

    def audio(self, script_id, language="en"):
        return self.post(
            f"/scripts/{script_id}/audio",
            {"language": language, "voice_id": "mock-chime", "speed": 1},
        )["job"]
