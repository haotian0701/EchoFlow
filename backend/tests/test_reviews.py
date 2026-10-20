import uuid

import pytest

from app.models import Evaluation, ScriptVersion

TEXT = ("Revenue rose by 12 percent in Berlin according to the annual report. " * 8).strip()
CONFIG = {
    "target_language": "en",
    "length": "short",
    "style": "news",
    "audience": "general",
}


async def seed_script(client):
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
    async with client.test_sessions() as session:
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


@pytest.mark.asyncio
async def test_manual_evaluation_idempotency_and_queries(client):
    _, script = await seed_script(client)
    key = str(uuid.uuid4())
    created = await client.post(
        f"/scripts/{script.id}/evaluate", headers={"Idempotency-Key": key}
    )
    assert created.status_code == 202
    body = created.json()
    assert body["job"]["job_type"] == "script_evaluation"
    assert body["evaluation"]["status"] == "queued"

    replay = await client.post(
        f"/scripts/{script.id}/evaluate", headers={"Idempotency-Key": key}
    )
    assert replay.status_code == 200
    assert replay.json()["evaluation"]["id"] == body["evaluation"]["id"]

    listing = await client.get(f"/scripts/{script.id}/evaluations")
    assert listing.json()["total"] == 1
    detail = await client.get(f"/evaluations/{body['evaluation']['id']}")
    assert detail.json()["issues"] == []


@pytest.mark.asyncio
async def test_edit_creates_version_and_rejects_stale_or_unchanged(client):
    _, script = await seed_script(client)
    unchanged = await client.patch(
        f"/scripts/{script.id}", json={"text": TEXT, "expected_version": 1}
    )
    assert unchanged.status_code == 422
    assert unchanged.json()["code"] == "SCRIPT_UNCHANGED"

    edited_text = TEXT.replace("12 percent", "13 percent", 1)
    edited = await client.patch(
        f"/scripts/{script.id}",
        json={"text": edited_text, "expected_version": 1},
    )
    assert edited.status_code == 201
    assert edited.json()["version"] == 2
    assert edited.json()["source"] == "edited"
    assert edited.json()["parent_version_id"] == str(script.id)

    stale = await client.patch(
        f"/scripts/{script.id}",
        json={"text": edited_text + " More detail.", "expected_version": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "SCRIPT_VERSION_CONFLICT"


@pytest.mark.asyncio
async def test_approval_requires_latest_successful_evaluation_and_acknowledgement(client):
    _, script = await seed_script(client)
    created = await client.post(f"/scripts/{script.id}/evaluate")
    evaluation_id = uuid.UUID(created.json()["evaluation"]["id"])
    critical_id = uuid.uuid4()
    async with client.test_sessions() as session:
        evaluation = await session.get(Evaluation, evaluation_id)
        evaluation.status = "succeeded"
        evaluation.factual_consistency = 0.7
        evaluation.content_coverage = 0.8
        evaluation.audio_readiness = 0.9
        evaluation.issues = [
            {
                "id": str(critical_id),
                "origin": "judge",
                "dimension": "factual_consistency",
                "severity": "critical",
                "title": "Unsupported statement",
                "explanation": "Confirm before approval.",
                "script_quote": None,
                "script_start": None,
                "script_end": None,
                "article_evidence_quote": None,
                "evidence_start": None,
                "evidence_end": None,
                "evidence_status": "insufficient",
            }
        ]
        await session.commit()

    missing = await client.post(
        f"/scripts/{script.id}/approve",
        json={
            "evaluation_id": str(evaluation_id),
            "expected_version": 1,
            "acknowledged_critical_issue_ids": [],
        },
    )
    assert missing.status_code == 409
    assert missing.json()["code"] == "CRITICAL_ISSUES_UNACKNOWLEDGED"

    approved = await client.post(
        f"/scripts/{script.id}/approve",
        json={
            "evaluation_id": str(evaluation_id),
            "expected_version": 1,
            "acknowledged_critical_issue_ids": [str(critical_id)],
        },
    )
    assert approved.status_code == 201
    replay = await client.post(
        f"/scripts/{script.id}/approve",
        json={
            "evaluation_id": str(evaluation_id),
            "expected_version": 1,
            "acknowledged_critical_issue_ids": [str(critical_id)],
        },
    )
    assert replay.status_code == 200
    history = await client.get(f"/scripts/{script.id}/approvals")
    assert len(history.json()["items"]) == 1
