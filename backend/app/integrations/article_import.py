import asyncio
import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from trafilatura import bare_extraction

from app.core.errors import ApiError

MAX_REDIRECTS = 3
MAX_HTML_BYTES = 2 * 1024 * 1024
FETCH_DEADLINE_SECONDS = 10


@dataclass(frozen=True)
class ExtractedArticle:
    title: str | None
    text: str
    final_url: str


def parse_public_url(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError) as exc:
        raise ApiError(
            422, "INVALID_URL", "Enter a valid public HTTP or HTTPS URL"
        ) from exc
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.username
        or url.password
        or url.port not in {None, 443 if url.scheme == "https" else 80}
    ):
        raise ApiError(
            422, "INVALID_URL", "Enter a public HTTP or HTTPS URL on a standard port"
        )
    return url


async def resolve_public_ip(host: str, port: int) -> str:
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise ApiError(
            502, "FETCH_FAILED", "The article host could not be resolved"
        ) from exc
    ips = {item[4][0] for item in addresses}
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ApiError(
            422, "INVALID_URL", "The URL must resolve only to public addresses"
        )
    return min(ips, key=lambda ip: (ipaddress.ip_address(ip).version != 4, ip))


async def fetch_public_html(value: str) -> tuple[str, str]:
    current = parse_public_url(value)
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ApiError(
                    502, "FETCH_TIMEOUT", "The article page took too long to load"
                )
            ip = await resolve_public_ip(
                current.host, current.port or (443 if current.scheme == "https" else 80)
            )
            pinned = current.copy_with(host=ip)
            default_port = 443 if current.scheme == "https" else 80
            display_host = f"[{current.host}]" if ":" in current.host else current.host
            host_header = (
                display_host
                if current.port in {None, default_port}
                else f"{display_host}:{current.port}"
            )
            request = httpx.Request(
                "GET",
                pinned,
                headers={
                    "Host": host_header,
                    "Accept": "text/html,application/xhtml+xml",
                },
                extensions={
                    "sni_hostname": current.host,
                    "timeout": {
                        "connect": remaining,
                        "read": remaining,
                        "write": remaining,
                        "pool": remaining,
                    },
                },
            )
            try:
                response = await client.send(request, stream=True)
                try:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ApiError(
                                502,
                                "FETCH_FAILED",
                                "The page redirected without a destination",
                            )
                        if redirect_count == MAX_REDIRECTS:
                            raise ApiError(
                                502,
                                "TOO_MANY_REDIRECTS",
                                "The page redirected too many times",
                            )
                        current = parse_public_url(urljoin(str(current), location))
                        continue
                    if response.status_code >= 400:
                        raise ApiError(
                            502,
                            "FETCH_FAILED",
                            f"The page returned HTTP {response.status_code}",
                        )
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise ApiError(
                            502,
                            "UNSUPPORTED_CONTENT",
                            "The URL did not return an HTML page",
                        )
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_HTML_BYTES:
                            raise ApiError(
                                413,
                                "PAGE_TOO_LARGE",
                                "The HTML page exceeds the 2 MiB limit",
                            )
                        if time.monotonic() > deadline:
                            raise ApiError(
                                502,
                                "FETCH_TIMEOUT",
                                "The article page took too long to load",
                            )
                    return chunks.decode(
                        response.charset_encoding or "utf-8", errors="replace"
                    ), str(current)
                finally:
                    await response.aclose()
            except httpx.TimeoutException as exc:
                raise ApiError(
                    502, "FETCH_TIMEOUT", "The article page took too long to load"
                ) from exc
            except httpx.RequestError as exc:
                raise ApiError(
                    502, "FETCH_FAILED", "The article page could not be fetched"
                ) from exc
    raise ApiError(502, "TOO_MANY_REDIRECTS", "The page redirected too many times")


async def import_article(value: str) -> ExtractedArticle:
    async def fetch_and_extract():
        html, final_url = await fetch_public_html(value)
        try:
            document = await asyncio.to_thread(
                bare_extraction,
                html,
                url=final_url,
                with_metadata=True,
                include_comments=False,
                include_tables=False,
            )
        except Exception as exc:
            raise ApiError(
                502, "EXTRACTION_FAILED", "The article text could not be extracted"
            ) from exc
        return document, final_url

    try:
        document, final_url = await asyncio.wait_for(
            fetch_and_extract(), timeout=FETCH_DEADLINE_SECONDS
        )
    except TimeoutError as exc:
        raise ApiError(
            502, "FETCH_TIMEOUT", "The article page took too long to load"
        ) from exc
    if document is None or not document.text or len(document.text.strip()) < 100:
        raise ApiError(
            502,
            "EXTRACTION_FAILED",
            "No usable article text was found; paste the text instead",
        )
    if len(document.text) > 30000:
        raise ApiError(
            413, "ARTICLE_TOO_LARGE", "The extracted article exceeds 30,000 characters"
        )
    return ExtractedArticle(
        title=document.title, text=document.text, final_url=final_url
    )
