"""Explicit test-data builders; no imports from test cases."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models.articles import Article
from app.models.jobs import JobExecution, ProductionJob, TaskOutbox
from app.models.reviews import Evaluation
from app.models.scripts import ScriptVersion
from app.services.script_production import process_generation_job
from tests.fakes import FailingProvider

CHIME = Path(__file__).resolve().parents[1] / "app/assets/mock-chime.mp3"
BODY = {"language": "en", "voice_id": "mock-chime", "speed": 1}
TEXT = (
    "Revenue rose by 12 percent in Berlin according to the annual report. " * 8
).strip()
CONFIG = {
    "target_language": "en",
    "length": "short",
    "style": "news",
    "audience": "general",
}


async def seed_job(sessions, *, article_id=None, source_script_id=None):
    async with sessions() as session:
        if article_id is None:
            article = Article(
                title="Source report",
                source="Newsroom",
                original_text=("Revenue grew by 12 percent in Berlin. " * 5).strip(),
                language="en",
                import_type="manual",
                content_hash=uuid.uuid4().hex.ljust(64, "0"),
            )
            session.add(article)
            await session.flush()
            article_id = article.id
        job = ProductionJob(
            job_type="script_generation",
            article_id=article_id,
            source_script_version_id=source_script_id,
            status="queued",
            stage="queued",
            config={
                "target_language": "en",
                "length": "short",
                "style": "news",
                "audience": "general",
            },
            input_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
            attempt_count=0,
            max_attempts=3,
        )
        session.add(job)
        await session.flush()
        session.add(TaskOutbox(job_id=job.id, task_name="test", status="dispatched"))
        await session.commit()
        return article_id, job.id


async def seed_evaluation_job(sessions):
    async with sessions() as session:
        article = Article(
            title="Report",
            source="Newsroom",
            original_text=("Revenue rose by 12 percent in Berlin. " * 15).strip(),
            language="en",
            import_type="manual",
            content_hash=uuid.uuid4().hex.ljust(64, "0"),
        )
        session.add(article)
        await session.flush()
        script = ScriptVersion(
            article_id=article.id,
            version=1,
            text=("Revenue rose by 12 percent in Berlin. " * 10).strip(),
            source="generated",
            target_language="en",
            generation_config={
                "target_language": "en",
                "length": "short",
                "style": "news",
                "audience": "general",
            },
            provider="mock",
            requested_model="mock/script-v1",
            actual_model="mock/script-v1",
            prompt_version="script-generation-v1",
            estimated_duration_seconds=120,
        )
        session.add(script)
        await session.flush()
        job = ProductionJob(
            job_type="script_evaluation",
            article_id=article.id,
            source_script_version_id=script.id,
            status="queued",
            stage="queued",
            config={
                "rule_version": "evaluation-rules-v1",
                "prompt_version": "script-evaluation-v1",
            },
            input_fingerprint="c" * 64,
            attempt_count=0,
            max_attempts=3,
        )
        session.add(job)
        await session.flush()
        evaluation = Evaluation(
            script_version_id=script.id,
            job_id=job.id,
            status="queued",
            issues=[],
            rule_version="evaluation-rules-v1",
            prompt_version="script-evaluation-v1",
        )
        session.add(evaluation)
        await session.flush()
        job.result_evaluation_id = evaluation.id
        session.add(
            TaskOutbox(
                job_id=job.id,
                task_name="test",
                queue_name="script_evaluation",
                status="dispatched",
            )
        )
        await session.commit()
        return job.id, evaluation.id


async def approved_script(client, sessions):
    article, script = await seed_script(client, sessions)
    evaluated = await client.post(f"/scripts/{script.id}/evaluate")
    evaluation_id = uuid.UUID(evaluated.json()["evaluation"]["id"])
    async with sessions() as session:
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


async def seed_script(client, sessions):
    article_response = await client.post(
        "/articles",
        json={
            "title": "Annual report",
            "source": "Newsroom",
            "text": TEXT,
            "language": "en",
        },
    )
    article = article_response.json()["article"]
    async with sessions() as session:
        script = ScriptVersion(
            article_id=uuid.UUID(article["id"]),
            version=1,
            text=TEXT,
            source="generated",
            target_language="en",
            generation_config=CONFIG,
            provider="mock",
            requested_model="mock/script-v1",
            actual_model="mock/script-v1",
            prompt_version="script-generation-v1",
            estimated_duration_seconds=120,
        )
        session.add(script)
        await session.commit()
        await session.refresh(script)
        return article, script


async def failed_generation(sessions, ambiguous=False):
    _, job_id = await seed_job(sessions)
    await process_generation_job(
        job_id,
        session_factory=sessions,
        provider=FailingProvider(ambiguous=ambiguous),
    )
    return job_id


async def expire(sessions, execution_id):
    async with sessions() as session:
        execution = await session.get(JobExecution, execution_id)
        execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
