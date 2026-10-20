import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.audio import AudioAsset
from app.models.jobs import ProductionJob, ProviderCall
from app.services.audio_production import process_audio_job
from tests.factories import BODY, approved_script
from tests.fakes import MemoryStorage

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_audio_reuse_duplicate_delivery_and_constraints(
    postgres_sessions, postgres_client
):
    sessions, client = postgres_sessions, postgres_client
    _, script = await approved_script(client, sessions)
    url = f"/scripts/{script.id}/audio"
    keys = [str(uuid.uuid4()) for _ in range(6)]
    responses = await asyncio.gather(
        *[client.post(url, json=BODY, headers={"Idempotency-Key": key}) for key in keys]
    )
    assert sorted(result.status_code for result in responses) == [200] * 5 + [202]
    assert len({result.json()["job"]["id"] for result in responses}) == 1
    job_id = uuid.UUID(responses[0].json()["job"]["id"])
    conflicts = await asyncio.gather(
        *[
            client.post(
                url, json={**BODY, "speed": 1.1}, headers={"Idempotency-Key": keys[0]}
            )
            for _ in range(2)
        ]
    )
    assert all(result.status_code == 409 for result in conflicts)
    storage = MemoryStorage()
    await asyncio.gather(
        *[
            process_audio_job(job_id, session_factory=sessions, storage=storage)
            for _ in range(2)
        ]
    )
    assert storage.upload_attempts == 1
    async with sessions() as session:
        job = await session.get(ProductionJob, job_id)
        assert job.status == "succeeded"
        assert (
            len(
                (
                    await session.scalars(
                        select(ProviderCall).where(ProviderCall.job_id == job_id)
                    )
                ).all()
            )
            == 1
        )
        asset = await session.get(AudioAsset, job.result_audio_asset_id)
        audio_id = asset.id
        asset.approval_id = uuid.uuid4()
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        asset = await session.get(AudioAsset, audio_id)
        data = {
            column.name: getattr(asset, column.name)
            for column in AudioAsset.__table__.columns
        }
        data["id"] = uuid.uuid4()
        session.add(AudioAsset(**data))
        with pytest.raises(IntegrityError):
            await session.commit()
