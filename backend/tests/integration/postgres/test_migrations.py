import uuid

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_stage3_data_and_stage4_running_jobs_survive_upgrade(postgres_database):
    engine, migrate = postgres_database
    await migrate("0003_evaluation_review")
    article_id, job_id = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("""INSERT INTO articles
            (id,title,source,original_text,language,import_type,content_hash)
            VALUES (:id,'Before Stage 4','Test','Persisted Stage 3 article','en','manual',:hash)"""),
            {"id": article_id, "hash": uuid.uuid4().hex.ljust(64, "0")},
        )
    await migrate("0004_audio_delivery")
    async with engine.begin() as connection:
        await connection.execute(
            text("""INSERT INTO production_jobs
            (id,job_type,article_id,status,stage,config,input_fingerprint,attempt_count,max_attempts)
            VALUES (:id,'script_generation',:article,'running','provider_call','{}','legacy',1,3)"""),
            {"id": job_id, "article": article_id},
        )
        await connection.execute(
            text("""INSERT INTO provider_calls
            (id,job_id,operation,provider,requested_model,status)
            VALUES (:id,:job,'script_generation','mock','mock/script-v1','running')"""),
            {"id": uuid.uuid4(), "job": job_id},
        )
    await migrate("head")
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT title FROM articles WHERE id=:id"), {"id": article_id}
            )
            == "Before Stage 4"
        )
        assert (
            await connection.scalar(
                text("SELECT status FROM production_jobs WHERE id=:id"), {"id": job_id}
            )
            == "needs_attention"
        )
        assert (
            await connection.scalar(
                text("SELECT status FROM provider_calls WHERE job_id=:id"),
                {"id": job_id},
            )
            == "unknown"
        )
