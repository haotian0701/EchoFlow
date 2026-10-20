import uuid

import pytest
from sqlalchemy import func, select

from app.models.reviews import Evaluation
from app.services.script_production import (
    process_evaluation_job,
    process_generation_job,
)
from tests.factories import approved_script, failed_generation
from tests.fakes import FailingProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_manual_retry_idempotency_charge_ack_and_linear_history(
    sessions, client, ambiguous
):
    job_id = await failed_generation(sessions, ambiguous)
    key = str(uuid.uuid4())
    url = f"/jobs/{job_id}/retry"
    body = {"acknowledge_possible_charge": False}
    if ambiguous:
        result = await client.post(url, json=body, headers={"Idempotency-Key": key})
        assert (
            result.status_code == 409
            and result.json()["code"] == "CHARGE_ACKNOWLEDGEMENT_REQUIRED"
        )
        body["acknowledge_possible_charge"] = True
    first = await client.post(url, json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    child = first.json()["job"]
    assert child["retry_of_job_id"] == str(job_id) and child["attempt_count"] == 0
    assert (
        child["root_job_id"] == str(job_id)
        and child["provider_snapshot"]["llm_provider"] == "mock"
    )
    for other_key in (key, str(uuid.uuid4())):
        replay = await client.post(
            url, json=body, headers={"Idempotency-Key": other_key}
        )
        assert replay.status_code == 200 and replay.json()["job"]["id"] == child["id"]
    conflict = await client.post(
        url,
        json={"acknowledge_possible_charge": not body["acknowledge_possible_charge"]},
        headers={"Idempotency-Key": key},
    )
    assert (
        conflict.status_code == 409
        and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    )
    assert (await client.get(f"/jobs/{job_id}")).json()["status"] == (
        "needs_attention" if ambiguous else "failed"
    )
    await process_generation_job(
        uuid.UUID(child["id"]),
        session_factory=sessions,
        provider=FailingProvider(),
    )
    grandchild = (await client.post(f"/jobs/{child['id']}/retry", json={})).json()[
        "job"
    ]
    assert grandchild["retry_of_job_id"] == child["id"] and grandchild[
        "root_job_id"
    ] == str(job_id)


@pytest.mark.asyncio
async def test_evaluation_retry_creates_new_report_and_rejects_stale_attempt(
    sessions, client
):
    _, script = await approved_script(client, sessions)
    request = await client.post(f"/scripts/{script.id}/evaluate")
    job_id = uuid.UUID(request.json()["job"]["id"])

    class Failure:
        name = "mock"
        requested_model = "mock/evaluator-v1"

        async def evaluate_script(self, *args):
            from app.core.provider_errors import ProviderError

            raise ProviderError("JUDGE_FAILED", "Invalid judge output")

    await process_evaluation_job(job_id, session_factory=sessions, provider=Failure())
    retried = await client.post(f"/jobs/{job_id}/retry", json={})
    assert retried.status_code == 202, retried.text
    assert (
        retried.json()["job"]["result_evaluation_id"]
        != request.json()["evaluation"]["id"]
    )
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Evaluation)
                .where(Evaluation.script_version_id == script.id)
            )
            == 3
        )
