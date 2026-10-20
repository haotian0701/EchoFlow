import pytest

from app.models.jobs import ProviderCall
from app.services.execution import claim, fenced
from app.services.recovery import recover_once
from tests.factories import expire, seed_job


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True])
async def test_expired_lease_classifies_evidence_and_fences_late_worker(
    sessions, client, prepared
):
    _, job_id = await seed_job(sessions)
    execution_id = await claim(sessions, job_id, "script_generation")
    assert await claim(sessions, job_id, "script_generation") is None
    if prepared:
        async with sessions() as session:
            session.add(
                ProviderCall(
                    job_id=job_id,
                    execution_id=execution_id,
                    provider="mock",
                    requested_model="mock/script-v1",
                    status="running",
                )
            )
            await session.commit()
    await expire(sessions, execution_id)
    async with sessions() as session:
        assert await fenced(session, job_id, execution_id) is None
    await recover_once(sessions)
    result = (await client.get(f"/jobs/{job_id}")).json()
    assert result["status"] == ("needs_attention" if prepared else "queued")
    executions = (await client.get(f"/jobs/{job_id}/executions")).json()
    assert executions["items"][0]["status"] == "abandoned"
    if prepared:
        assert (await client.get(f"/jobs/{job_id}/calls")).json()["items"][0][
            "status"
        ] == "unknown"
    else:
        new_execution = await claim(sessions, job_id, "script_generation")
        assert new_execution != execution_id
        async with sessions() as session:
            assert await fenced(session, job_id, execution_id) is None
