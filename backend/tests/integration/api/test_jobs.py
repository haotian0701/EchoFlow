import uuid

import pytest

TEXT = (
    "A sourced report contains names, dates, and figures that must remain accurate. "
    * 3
).strip()
CONFIG = {
    "target_language": "en",
    "length": "short",
    "style": "news",
    "audience": "general",
}


async def create_article(client):
    response = await client.post(
        "/articles",
        json={
            "title": "Source story",
            "source": "Newsroom",
            "text": TEXT,
            "language": "en",
        },
    )
    return response.json()["article"]


@pytest.mark.asyncio
async def test_create_job_idempotency_and_queries(client):
    article = await create_article(client)
    key = str(uuid.uuid4())
    body = {"type": "script_generation", "article_id": article["id"], "config": CONFIG}

    created = await client.post("/jobs", json=body, headers={"Idempotency-Key": key})
    assert created.status_code == 202
    assert created.headers["location"].startswith("/jobs/")
    job = created.json()["job"]
    assert job["status"] == "queued"
    assert created.json()["idempotent_replay"] is False

    replay = await client.post("/jobs", json=body, headers={"Idempotency-Key": key})
    assert replay.status_code == 200
    assert replay.json()["job"]["id"] == job["id"]
    assert replay.json()["idempotent_replay"] is True

    conflict = await client.post(
        "/jobs",
        json={**body, "config": {**CONFIG, "length": "long"}},
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    listing = await client.get(f"/jobs?article_id={article['id']}")
    assert listing.json()["total"] == 1
    detail = await client.get(f"/jobs/{job['id']}")
    assert detail.json()["config"] == CONFIG
    events = await client.get(f"/jobs/{job['id']}/events")
    assert events.json()["items"][0]["event_type"] == "created"
    assert events.json()["items"][0]["metadata"]["config"] == CONFIG


@pytest.mark.asyncio
async def test_job_validation_and_missing_article(client):
    missing = await client.post(
        "/jobs",
        json={
            "type": "script_generation",
            "article_id": str(uuid.uuid4()),
            "config": CONFIG,
        },
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "ARTICLE_NOT_FOUND"

    article = await create_article(client)
    invalid = await client.post(
        "/jobs",
        json={
            "type": "script_generation",
            "article_id": article["id"],
            "config": {**CONFIG, "style": "dramatic"},
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_empty_script_list_and_missing_script(client):
    article = await create_article(client)
    listing = await client.get(f"/articles/{article['id']}/scripts")
    assert listing.status_code == 200
    assert listing.json()["items"] == []
    missing = await client.get(f"/scripts/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "SCRIPT_NOT_FOUND"
