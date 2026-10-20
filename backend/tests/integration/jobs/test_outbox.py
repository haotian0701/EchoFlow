from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.jobs import ProductionJob, TaskOutbox
from app.services.dispatch import dispatch_once
from app.services.recovery import recover_once
from app.services.script_production import (
    process_generation_job,
)
from tests.factories import seed_job
from tests.fakes import SuccessfulProvider


@pytest.mark.asyncio
async def test_outbox_commits_runnable_state_before_publish_and_retains_broker_failure(
    sessions,
    client,
):
    _, job_id = await seed_job(sessions)
    async with sessions() as session:
        job = await session.get(ProductionJob, job_id)
        job.status = "retry_scheduled"
        row = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        row.status = "pending"
        await session.commit()
    sent = []

    def failure(*args, **kwargs):
        sent.append(kwargs)
        raise OSError("secret upstream URL must not be retained")

    assert await dispatch_once(sessions, failure) == 0
    async with sessions() as session:
        assert (await session.get(ProductionJob, job_id)).status == "queued"
        row = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        assert row.status == "pending" and "secret" not in row.last_error
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert await dispatch_once(sessions, lambda *a, **kw: sent.append(kw)) == 1
    async with sessions() as session:
        row = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        row.dispatched_at = datetime.now(UTC) - timedelta(seconds=121)
        await session.commit()
    await recover_once(sessions)
    async with sessions() as session:
        assert (
            await session.scalar(select(TaskOutbox).where(TaskOutbox.job_id == job_id))
        ).status == "pending"


@pytest.mark.asyncio
async def test_publish_success_commit_failure_allows_duplicate_without_duplicate_result(
    sessions, client, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession

    _, job_id = await seed_job(sessions)
    async with sessions() as session:
        outbox = await session.scalar(
            select(TaskOutbox).where(TaskOutbox.job_id == job_id)
        )
        outbox.status = "pending"
        await session.commit()
    original = AsyncSession.commit
    once = True

    async def commit(session):
        nonlocal once
        if once and any(
            isinstance(row, TaskOutbox) and row.status == "dispatched"
            for row in session.dirty
        ):
            once = False
            raise OSError("Controlled interruption after broker accepted the message")
        return await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    sent = []
    with pytest.raises(OSError):
        await dispatch_once(sessions, lambda *a, **kw: sent.append(kw))
    assert await dispatch_once(sessions, lambda *a, **kw: sent.append(kw)) == 1
    assert len(sent) == 2
    for _ in sent:
        await process_generation_job(
            job_id, session_factory=sessions, provider=SuccessfulProvider()
        )
    assert (await client.get(f"/jobs/{job_id}/calls")).json()["total"] == 1
