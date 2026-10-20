"""Fault injection entrypoint used exclusively by compose.recovery.yaml."""

import asyncio
import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.provider_errors import ProviderError
from app.integrations.llm.generation import MockProvider
from app.models.articles import Article
from app.models.jobs import ProductionJob
from app.services import audio_production, execution
from app.tasks.celery_app import celery_app


def run():
    settings = get_settings()
    if (
        settings.llm_provider != "mock"
        or settings.tts_provider != "mock"
        or os.getenv("ECHOFLOW_E2E_RECOVERY") != "1"
    ):
        raise SystemExit(
            "Fault injection requires explicitly enabled, fully Mock E2E mode"
        )
    # Celery tasks use independent event loops; the harness must not reuse pooled connections.
    sessions = async_sessionmaker(
        create_async_engine(settings.database_url, poolclass=NullPool),
        expire_on_commit=False,
    )
    original_claim, original_generate, original_upload = (
        execution.claim,
        MockProvider.generate_script,
        audio_production.upload_artifact,
    )

    async def claim(factory, job_id, kind):
        execution_id = await original_claim(factory, job_id, kind)
        if execution_id:
            async with factory() as session:
                job = await session.get(ProductionJob, job_id)
                article = await session.get(Article, job.article_id)
            if article.source == "e2e-before-call" and job.attempt_count == 1:
                await asyncio.sleep(300)
        return execution_id

    async def generate(provider, article, config, job_id):
        async with sessions() as session:
            job = await session.get(ProductionJob, job_id)
        if article.source == "e2e-during-call" and not job.retry_of_job_id:
            await asyncio.sleep(300)
        if article.source == "e2e-transient" and job.attempt_count == 1:
            raise ProviderError(
                "E2E_TEMPORARY", "Controlled Mock temporary failure", retryable=True
            )
        return await original_generate(provider, article, config, job_id)

    async def upload(job_id, execution_id, artifact, factory, storage=None):
        async with factory() as session:
            job = await session.get(ProductionJob, job_id)
            article = await session.get(Article, job.article_id)
        if article.source == "e2e-after-checkpoint" and job.attempt_count == 1:
            await asyncio.sleep(300)
        return await original_upload(job_id, execution_id, artifact, factory, storage)

    execution.claim = claim
    MockProvider.generate_script = generate
    audio_production.upload_artifact = upload
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=INFO",
            "--concurrency=2",
            "--queues=script_generation,script_evaluation,audio_generation",
        ]
    )


if __name__ == "__main__":
    run()
