"""Opt-in PostgreSQL migration and concurrency test, using a new temporary DB.

Set TEST_POSTGRES_URL to a local PostgreSQL admin URL with CREATEDB permission.
The test never modifies the database named by that URL; it creates and drops its
own randomly named echoflow_stage4_test_* database.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.audio_worker import process_audio_job
from app.config import Settings
from app.database import get_session
from app.execution import claim, fenced
from app.main import app
from app.maintenance import recover_once
from app.models import AudioAsset, JobExecution, ProductionJob, ProviderCall
from app.worker import process_generation_job
from tests.test_audio import BODY, MemoryStorage, approved_script
from tests.test_worker import FailingProvider, seed_job


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Optional PostgreSQL migration/concurrency check",
)
@pytest.mark.asyncio
async def test_stage3_migration_concurrent_requests_and_duplicate_delivery(monkeypatch):
    monkeypatch.setattr(
        "app.tts.get_settings", lambda: Settings(_env_file=None, tts_provider="mock")
    )
    admin_url = make_url(os.environ["TEST_POSTGRES_URL"])
    db_name = f"echoflow_stage4_test_{uuid.uuid4().hex}"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    engine = None
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{db_name}"'))
        target_url = admin_url.set(database=db_name)
        env = {
            **os.environ,
            "DATABASE_URL": target_url.render_as_string(hide_password=False),
        }
        backend = Path(__file__).parents[1]

        def migrate(revision):
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", revision],
                cwd=backend,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

        await asyncio.to_thread(migrate, "0003_evaluation_review")
        engine = create_async_engine(target_url)
        old_id = uuid.uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO articles
                (id,title,source,original_text,language,import_type,content_hash)
                VALUES (:id,'Before Stage 4','Test','Persisted Stage 3 article','en','manual',:hash)"""),
                {"id": old_id, "hash": uuid.uuid4().hex.ljust(64, "0")},
            )
        await asyncio.to_thread(migrate, "0004_audio_delivery")
        legacy_job = uuid.uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO production_jobs
                (id,job_type,article_id,status,stage,config,input_fingerprint,attempt_count,max_attempts)
                VALUES (:id,'script_generation',:article,'running','provider_call','{}',:hash,1,3)"""),
                {"id": legacy_job, "article": old_id, "hash": "legacy"},
            )
            await connection.execute(
                text("""INSERT INTO provider_calls
                (id,job_id,operation,provider,requested_model,status)
                VALUES (:id,:job,'script_generation','mock','mock/script-v1','running')"""),
                {"id": uuid.uuid4(), "job": legacy_job},
            )
        await asyncio.to_thread(migrate, "head")
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT status FROM production_jobs WHERE id=:id"),
                    {"id": legacy_job},
                )
                == "needs_attention"
            )
            assert (
                await connection.scalar(
                    text("SELECT status FROM provider_calls WHERE job_id=:id"),
                    {"id": legacy_job},
                )
                == "unknown"
            )
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT title FROM articles WHERE id=:id"), {"id": old_id}
                )
                == "Before Stage 4"
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def session_override():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            client.test_sessions = sessions
            _, generation_id = await seed_job(sessions)
            claims = await asyncio.gather(
                *[claim(sessions, generation_id, "script_generation") for _ in range(8)]
            )
            assert sum(value is not None for value in claims) == 1
            execution_id = next(value for value in claims if value)
            async with sessions() as session:
                execution = await session.get(JobExecution, execution_id)
                execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()
            await recover_once(sessions)
            async with sessions() as session:
                assert await fenced(session, generation_id, execution_id) is None
            await process_generation_job(
                generation_id, session_factory=sessions, provider=FailingProvider()
            )
            retry_key = str(uuid.uuid4())
            retries = await asyncio.gather(
                *[
                    client.post(
                        f"/jobs/{generation_id}/retry",
                        json={},
                        headers={"Idempotency-Key": retry_key},
                    )
                    for _ in range(8)
                ]
            )
            assert sorted(result.status_code for result in retries) == [200] * 7 + [
                202
            ], [result.text for result in retries]
            assert len({result.json()["job"]["id"] for result in retries}) == 1
            _, script = await approved_script(client)
            url = f"/scripts/{script.id}/audio"
            keys = [str(uuid.uuid4()) for _ in range(6)]
            responses = await asyncio.gather(
                *[
                    client.post(url, json=BODY, headers={"Idempotency-Key": key})
                    for key in keys
                ]
            )
            assert sorted(response.status_code for response in responses) == [
                200
            ] * 5 + [202]
            assert len({response.json()["job"]["id"] for response in responses}) == 1
            job_id = uuid.UUID(responses[0].json()["job"]["id"])
            key = keys[0]
            conflicts = await asyncio.gather(
                *[
                    client.post(
                        url,
                        json={**BODY, "speed": 1.1},
                        headers={"Idempotency-Key": key},
                    )
                    for _ in range(2)
                ]
            )
            assert all(response.status_code == 409 for response in conflicts)
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
                calls = (
                    await session.scalars(
                        select(ProviderCall).where(ProviderCall.job_id == job_id)
                    )
                ).all()
                assert len(calls) == 1
                # Verify migration-created FK and per-job unique constraint.
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
    finally:
        app.dependency_overrides.clear()
        if engine:
            await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            )
        await admin.dispose()
