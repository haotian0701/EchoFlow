import re

import pytest

from tests.e2e.support import API_URL, WEB_URL

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def test_monitor_review_audio_and_mobile_dashboard(production, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    article, script = production.approved(source="e2e-browser")
    job = production.wait(production.audio(script["id"])["id"])
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(WEB_URL + "/jobs", wait_until="domcontentloaded")
            page.get_by_label("Article title", exact=True).fill(article["title"])
            playwright.expect(page.locator(".monitor-table tbody tr")).to_have_count(3)
            page.goto(
                WEB_URL + f"/review/{script['id']}", wait_until="domcontentloaded"
            )
            page.get_by_role("link", name=re.compile("Job details")).click()
            playwright.expect(page.locator(".monitor-detail-heading")).to_be_visible()
            page.reload(wait_until="domcontentloaded")
            playwright.expect(
                page.get_by_role("heading", name="Provider calls", exact=True)
            ).to_be_visible()

            failed = [False]

            def temporarily_unavailable(route):
                if not failed[0]:
                    failed[0] = True
                    route.fulfill(
                        status=503,
                        headers={"Access-Control-Allow-Origin": WEB_URL},
                        json={
                            "code": "E2E_TEMPORARY",
                            "message": "Controlled temporary error",
                            "details": {},
                            "request_id": "e2e",
                        },
                    )
                else:
                    route.continue_()

            page.route(API_URL + f"/jobs/{job['id']}", temporarily_unavailable)
            page.goto(WEB_URL + f"/jobs/{job['id']}", wait_until="domcontentloaded")
            playwright.expect(page.locator(".monitor-detail-heading")).to_be_visible(
                timeout=12000
            )
            assert failed[0]
            page.goto(WEB_URL + "/dashboard", wait_until="domcontentloaded")
            playwright.expect(
                page.get_by_role("heading", name="Recorded provider usage")
            ).to_be_visible()
            page.get_by_label("Time range", exact=True).select_option("all")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            page.screenshot(path=str(tmp_path / "dashboard-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(WEB_URL + f"/audio/{script['id']}", wait_until="domcontentloaded")
            playwright.expect(page.locator("article.audio-item").first).to_be_visible()
            with page.expect_download() as pending:
                page.get_by_role("link", name=re.compile("Download MP3")).first.click()
            pending.value.save_as(tmp_path / "audio.mp3")
            assert (tmp_path / "audio.mp3").stat().st_size > 100
            assert not errors, errors
        finally:
            browser.close()
