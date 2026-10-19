import pytest
from app import articles
from app.errors import ApiError
from app.importer import ExtractedArticle

TEXT = ("A clear source article with several verifiable details. " * 4).strip()


@pytest.mark.asyncio
async def test_manual_create_duplicate_list_and_detail(client):
    body = {
        "title": "Morning report",
        "source": "Newsroom",
        "text": TEXT,
        "language": "en",
    }
    created = await client.post("/articles", json=body)
    assert created.status_code == 201
    saved = created.json()["article"]
    assert saved["original_text"] == TEXT
    assert created.json()["duplicate"] is False
    assert created.headers["x-request-id"]

    same_content = await client.post(
        "/articles", json={**body, "title": "Another title", "text": "  " + TEXT + "  "}
    )
    assert same_content.status_code == 200
    assert same_content.json()["duplicate"] is True
    assert same_content.json()["article"]["id"] == saved["id"]

    listing = await client.get("/articles?q=morning")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert "original_text" not in listing.json()["items"][0]
    detail = await client.get(f"/articles/{saved['id']}")
    assert detail.json()["original_text"] == TEXT
    missing = await client.get("/articles/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["code"] == "ARTICLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_input_validation(client):
    response = await client.post(
        "/articles",
        json={
            "title": "Only a title",
            "source": "News",
            "text": "too short",
            "language": "en",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["request_id"]


@pytest.mark.asyncio
async def test_import_success_duplicate_and_failed_history(client, monkeypatch):
    async def extracted(_url):
        return ExtractedArticle("Page title", TEXT, "https://news.example/story")

    monkeypatch.setattr(articles, "import_article", extracted)
    body = {"url": "https://news.example/story", "language": "de"}
    first = await client.post("/articles/import", json=body)
    assert first.status_code == 201
    assert first.json()["article"]["language"] == "de"
    assert first.json()["article"]["source"] == "news.example"
    assert first.json()["import_attempt"]["status"] == "succeeded"

    second = await client.post("/articles/import", json=body)
    assert second.status_code == 200
    assert second.json()["import_attempt"]["status"] == "duplicate"

    async def failed(_url):
        raise ApiError(502, "EXTRACTION_FAILED", "No usable article text was found")

    monkeypatch.setattr(articles, "import_article", failed)
    failure = await client.post(
        "/articles/import", json={"url": "https://news.example/bad", "language": "en"}
    )
    assert failure.status_code == 502
    assert failure.json()["code"] == "EXTRACTION_FAILED"
    assert failure.json()["details"]["import_attempt_id"]

    history = await client.get("/articles/imports")
    assert history.status_code == 200
    assert [item["status"] for item in history.json()["items"]] == [
        "failed",
        "duplicate",
        "succeeded",
    ]
