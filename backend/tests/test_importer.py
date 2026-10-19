import asyncio

import httpx
import pytest
from app import importer
from app.errors import ApiError


@pytest.mark.asyncio
async def test_rejects_private_address():
    with pytest.raises(ApiError) as error:
        await importer.resolve_public_ip("127.0.0.1", 80)
    assert error.value.status == 422


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_blocked(monkeypatch):
    original_resolver = importer.resolve_public_ip

    async def resolver(host, port):
        if host == "news.example":
            return "93.184.215.14"
        return await original_resolver(host, port)

    def handler(_request):
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(importer, "resolve_public_ip", resolver)
    monkeypatch.setattr(
        importer.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(ApiError) as error:
        await importer.fetch_public_html("https://news.example/story")
    assert error.value.code == "INVALID_URL"


@pytest.mark.asyncio
async def test_page_over_two_megabytes_is_rejected(monkeypatch):
    async def resolver(_host, _port):
        return "93.184.215.14"

    def handler(_request):
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=b"x" * (importer.MAX_HTML_BYTES + 1),
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(importer, "resolve_public_ip", resolver)
    monkeypatch.setattr(
        importer.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(ApiError) as error:
        await importer.fetch_public_html("https://news.example/story")
    assert error.value.status == 413
    assert error.value.code == "PAGE_TOO_LARGE"


@pytest.mark.asyncio
async def test_timeout_and_extraction_failure(monkeypatch):
    async def slow_fetch(_url):
        await asyncio.sleep(0.05)
        return "", "https://news.example/story"

    monkeypatch.setattr(importer, "fetch_public_html", slow_fetch)
    monkeypatch.setattr(importer, "FETCH_DEADLINE_SECONDS", 0.001)
    with pytest.raises(ApiError) as error:
        await importer.import_article("https://news.example/story")
    assert error.value.code == "FETCH_TIMEOUT"

    async def empty_page(_url):
        return "<html><body><nav>Menu</nav></body></html>", "https://news.example/story"

    monkeypatch.setattr(importer, "FETCH_DEADLINE_SECONDS", 10)
    monkeypatch.setattr(importer, "fetch_public_html", empty_page)
    with pytest.raises(ApiError) as error:
        await importer.import_article("https://news.example/story")
    assert error.value.code == "EXTRACTION_FAILED"
