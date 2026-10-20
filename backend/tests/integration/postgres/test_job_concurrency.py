import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.jobs import JobExecution
from app.services.execution import claim, fenced
from app.services.recovery import recover_once
from app.services.script_production import process_generation_job
from tests.factories import seed_job
from tests.fakes import FailingProvider

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_concurrent_claims_fencing_and_manual_retries(
    postgres_sessions, postgres_client
):
    sessions, client = postgres_sessions, postgres_client
    _, job_id = await seed_job(sessions)
    claims = await asyncio.gather(
        *[claim(sessions, job_id, "script_generation") for _ in range(8)]
    )
    assert sum(result is not None for result in claims) == 1
    execution_id = next(result for result in claims if result)
    async with sessions() as session:
        execution = await session.get(JobExecution, execution_id)
        execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    await recover_once(sessions)
    async with sessions() as session:
        assert await fenced(session, job_id, execution_id) is None
    await process_generation_job(
        job_id, session_factory=sessions, provider=FailingProvider()
    )
    key = str(uuid.uuid4())
    retries = await asyncio.gather(
        *[
            client.post(
                f"/jobs/{job_id}/retry", json={}, headers={"Idempotency-Key": key}
            )
            for _ in range(8)
        ]
    )
    assert sorted(result.status_code for result in retries) == [200] * 7 + [202], [
        r.text for r in retries
    ]
    assert len({result.json()["job"]["id"] for result in retries}) == 1
