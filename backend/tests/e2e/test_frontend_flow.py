"""User journeys against the isolated Mock stack; no paid provider is used."""

import re
import uuid
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from tests.e2e.support import API_URL, WEB_URL

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.fixture
def page(live_api, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            yield page, playwright.expect
            assert not errors, errors
        finally:
            page.screenshot(path=str(tmp_path / "frontend-flow.png"), full_page=True)
            browser.close()


def home(article, script_id=None, **extra):
    params = {"articleId": article["id"], "section": "production", **extra}
    if script_id:
        params["scriptId"] = script_id
    return WEB_URL + "/?" + urlencode(params)


def approve_visible(page, expect):
    expect(page.locator(".evaluation-card .score-grid")).to_be_visible(timeout=20000)
    for checkbox in page.locator(".acknowledge input").all():
        checkbox.check()
    page.get_by_role("button", name="Approve script", exact=True).click()
    expect(page.locator(".review-status")).to_have_text("Approved", timeout=15000)


def test_one_click_evaluation_approval_audio_and_restored_context(
    production, live_api, page, tmp_path
):
    page, expect = page
    page.set_viewport_size({"width": 390, "height": 844})
    title = (
        "ux-complete "
        + uuid.uuid4().hex[:8]
        + " Climate Change Lends New Color to the Ocean - NASA Science"
    )
    page.goto(
        WEB_URL + "/?q=ux-complete&offset=0&tab=articles", wait_until="domcontentloaded"
    )
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_label("Source", exact=True).fill("ux-complete")
    page.get_by_label("Article text", exact=False).fill(
        uuid.uuid4().hex
        + " Researchers in Berlin published a report in 2026. The study involved 120 participants. They announced a public meeting about the findings."
        * 15
    )
    page.get_by_role("button", name=re.compile("Save article")).click()
    expect(page).to_have_url(re.compile("articleId="))
    expect(page.locator(".generate-button")).to_be_enabled()
    expect(page.locator(".generate-button")).to_be_in_viewport(ratio=1)
    page.get_by_role("button", name=re.compile("^Generate script")).click()
    expect(page).to_have_url(re.compile("scriptId="), timeout=20000)
    origin = page.url
    script_id = parse_qs(urlparse(origin).query)["scriptId"][0]
    posts = []
    page.on(
        "request",
        lambda request: (
            posts.append(request.url)
            if request.method == "POST" and request.url.endswith("/evaluate")
            else None
        ),
    )
    expect(page.locator(".pending-tag")).to_have_text("Pending evaluation")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(
        page.get_by_role("button", name="Start evaluation", exact=True)
    ).to_be_in_viewport(ratio=1)
    page.get_by_role("button", name="Start evaluation", exact=True).click()
    expect(page).to_have_url(re.compile(f"/review/{script_id}"))
    approve_visible(page, expect)
    assert len(posts) == 1
    assert live_api.get(f"/scripts/{script_id}/evaluations").json()["total"] == 1

    page.get_by_role("link", name="Back to this article").click()
    expect(page.locator(".pending-tag")).to_have_text("Approved")
    expected_params = parse_qs(urlparse(origin).query)
    assert parse_qs(urlparse(page.url).query) == expected_params
    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".pending-tag")).to_have_text("Approved")
    page.get_by_role("link", name="Review", exact=True).click()
    page.get_by_role("button", name="Re-evaluate", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.get_by_role("button", name="Cancel", exact=True).click()
    assert len(posts) == 1
    expect(page.locator(".review-status")).to_have_text("Approved")
    page.get_by_role("button", name="Re-evaluate", exact=True).click()
    page.get_by_role("button", name="Start new evaluation", exact=True).click()
    expect(page.locator(".review-status")).to_have_text("Pending review", timeout=20000)
    expect(page.get_by_role("link", name="Produce audio", exact=True)).to_have_count(0)
    assert len(posts) == 2
    approve_visible(page, expect)
    page.locator(".toolbar-actions").get_by_role("link", name="Produce audio").click()
    expect(page.locator(".audio-production .source-tag")).to_have_text("Approved")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="Generate audio", exact=True).click()
    expect(page.locator(".audio-item")).to_have_count(1, timeout=20000)
    with page.expect_download() as pending:
        page.get_by_role("link", name="Download MP3").click()
    pending.value.save_as(tmp_path / "completed.mp3")
    assert (tmp_path / "completed.mp3").stat().st_size > 100
    page.locator(".audio-job").get_by_role("link", name=re.compile("Details")).click()
    page.reload(wait_until="domcontentloaded")
    page.get_by_role("link", name="Back to this article").click()
    expect(page).to_have_url(re.compile(f"/audio/{script_id}"))
    page.get_by_role("link", name="Back to this article").click()
    expect(page.locator(".pending-tag")).to_have_text("Approved")
    assert parse_qs(urlparse(page.url).query) == expected_params
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_unsaved_navigation_conflict_and_new_version_without_auto_evaluation(
    production, live_api, page
):
    page, expect = page
    article, script = production.approved("ux-draft")
    page.goto(home(article, script["id"]), wait_until="domcontentloaded")
    page.get_by_role("link", name="Review", exact=True).click()
    page.get_by_role("button", name="Edit script", exact=True).click()
    editor = page.get_by_role("textbox", name="Edit script text")
    draft = script["text"] + "\nA preserved editorial draft."
    editor.fill(draft)
    page.get_by_role("link", name=re.compile("Job details")).click()
    expect(page.get_by_role("dialog", name="Unsaved script changes")).to_be_visible()
    page.get_by_role("button", name="Keep editing").click()
    expect(editor).to_have_value(draft)

    # A native hard-refresh warning is separate from the SPA navigation blocker.
    playwright_error = pytest.importorskip("playwright.sync_api").Error
    dialogs = []

    def cancel_refresh(dialog):
        dialogs.append(dialog.type)
        dialog.dismiss()

    page.once("dialog", cancel_refresh)
    try:
        page.reload(wait_until="domcontentloaded", timeout=3000)
    except playwright_error:
        assert dialogs == ["beforeunload"]
    assert dialogs == ["beforeunload"]
    expect(editor).to_have_value(draft)
    page.go_back()
    expect(page.get_by_role("dialog", name="Unsaved script changes")).to_be_visible()
    page.get_by_role("button", name="Keep editing").click()
    expect(editor).to_have_value(draft)

    response = live_api.patch(
        f"/scripts/{script['id']}",
        json={
            "text": script["text"] + "\nA different saved edit.",
            "expected_version": script["version"],
        },
    )
    response.raise_for_status()
    latest = response.json()
    page.get_by_role("button", name="Save new version").click()
    expect(page.locator(".notice.error")).to_contain_text("Your draft is preserved")
    expect(editor).to_have_value(draft)
    page.locator(".review-toolbar select").select_option(latest["id"])
    page.get_by_role("button", name="Keep editing").click()
    expect(editor).to_have_value(draft)
    page.locator(".review-toolbar select").select_option(latest["id"])
    page.get_by_role("button", name="Discard changes").click()
    expect(page.locator(".review-status")).to_have_text("Pending evaluation")
    assert live_api.get(f"/scripts/{latest['id']}/evaluations").json()["total"] == 0
    page.get_by_role("button", name="Edit script", exact=True).click()
    editor.fill(latest["text"] + "\nSaved through the browser.")
    page.get_by_role("button", name="Save new version").click()
    expect(page.locator(".review-status")).to_have_text("Pending evaluation")
    expect(page.locator(".notice.success")).to_contain_text("New version saved")
    expect(editor).to_have_count(0)


def test_lost_evaluation_response_reuses_key_without_duplicate_job(
    production, live_api, page
):
    page, expect = page
    article, job = production.generate("ux-response-loss")
    script_id = production.wait(job["id"])["result_script_version_id"]
    keys = []

    def lose_first_response(route):
        keys.append(route.request.headers["idempotency-key"])
        response = route.fetch()
        assert response.status in (200, 202)
        if len(keys) == 1:
            route.abort("failed")
        else:
            route.fulfill(response=response)

    page.route(API_URL + f"/scripts/{script_id}/evaluate", lose_first_response)
    page.goto(home(article, script_id), wait_until="domcontentloaded")
    button = page.get_by_role("button", name="Start evaluation", exact=True)
    button.click()
    expect(button).to_be_enabled()
    button.click()
    expect(page).to_have_url(re.compile(f"/review/{script_id}"))
    expect(page.locator(".review-status")).to_have_text("Pending review", timeout=20000)
    assert len(keys) == 2 and keys[0] == keys[1]
    assert live_api.get(f"/scripts/{script_id}/evaluations").json()["total"] == 1


def test_long_unicode_evidence_is_visible_and_missing_evidence_is_explicit(
    production, live_api, page
):
    page, expect = page
    article, script = production.approved("ux-evidence")
    report = live_api.get(f"/scripts/{script['id']}/evaluations").json()["items"][0]
    full = live_api.get(f"/evaluations/{report['id']}").json()
    source_quote, script_quote = (
        "The final source evidence.",
        "The final script quotation.",
    )
    source_text = ("🌍 " + article["original_text"] + "\n\n") * 6 + source_quote
    script_text = ("🌍 " + script["text"] + "\n\n") * 5 + script_quote
    full["issues"] = [
        {
            "id": "located",
            "origin": "judge",
            "dimension": "factual_consistency",
            "severity": "warning",
            "title": "Located evidence",
            "explanation": "Controlled UI evidence fixture",
            "script_quote": script_quote,
            "script_start": len(script_text) - len(script_quote),
            "script_end": len(script_text),
            "article_evidence_quote": source_quote,
            "evidence_start": len(source_text) - len(source_quote),
            "evidence_end": len(source_text),
            "evidence_status": "verified",
        },
        {
            "id": "missing",
            "origin": "judge",
            "dimension": "factual_consistency",
            "severity": "warning",
            "title": "Missing evidence",
            "explanation": "Evidence is not located",
            "script_quote": None,
            "script_start": None,
            "script_end": None,
            "article_evidence_quote": None,
            "evidence_start": None,
            "evidence_end": None,
            "evidence_status": "insufficient",
        },
    ]
    page.route(
        API_URL + f"/articles/{article['id']}",
        lambda route: route.fulfill(
            headers={
                "Access-Control-Allow-Origin": WEB_URL,
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
            json={**article, "original_text": source_text},
        ),
    )
    page.route(
        API_URL + f"/scripts/{script['id']}",
        lambda route: route.fulfill(
            headers={
                "Access-Control-Allow-Origin": WEB_URL,
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
            json={**script, "text": script_text},
        ),
    )
    page.route(
        API_URL + f"/evaluations/{report['id']}",
        lambda route: route.fulfill(
            headers={
                "Access-Control-Allow-Origin": WEB_URL,
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
            json=full,
        ),
    )
    page.goto(WEB_URL + f"/review/{script['id']}", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile("Located evidence")).click()
    expect(page.locator(".review-text mark")).to_have_count(2)
    expect(page.locator('[aria-label="Script text"] mark')).to_have_text(script_quote)
    expect(page.locator('[aria-label="Source text"] mark')).to_have_text(source_quote)
    assert page.locator('[aria-label="Script text"] mark').evaluate(
        "e => { const r = e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight }"
    )
    page.get_by_role("button", name=re.compile("Missing evidence")).click()
    expect(page.locator("mark")).to_have_count(0)
    expect(page.locator(".evidence-badge")).to_have_text("insufficient")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name=re.compile("Located evidence")).click()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_delayed_article_response_does_not_replace_new_selection(
    production, live_api, page
):
    playwright_error = pytest.importorskip("playwright.sync_api").Error
    page, expect = page
    first, job = production.generate("ux-order-first")
    first_script = production.wait(job["id"])["result_script_version_id"]
    second, job = production.generate("ux-order-second")
    second_script = production.wait(job["id"])["result_script_version_id"]
    held = []
    page.route(API_URL + f"/scripts/{first_script}", lambda route: held.append(route))
    page.goto(home(first, first_script, q="ux-order"), wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile(re.escape(second["title"]))).click()
    expect(page.locator(".script-meta h3")).to_have_text("Script version 1")
    body = live_api.get(f"/scripts/{second_script}").json()["text"]
    expect(page.locator(".script-preview .script-body")).to_have_text(body)
    assert held
    for route in held:
        try:
            route.fulfill(
                headers={
                    "Access-Control-Allow-Origin": WEB_URL,
                    "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Allow-Methods": "GET, OPTIONS",
                },
                json=live_api.get(f"/scripts/{first_script}").json(),
            )
        except playwright_error as error:
            assert "closed" in str(error).lower() or "invalid" in str(error).lower()
    expect(page.locator(".script-preview .script-body")).to_have_text(body)
    assert parse_qs(urlparse(page.url).query)["articleId"] == [second["id"]]


def test_unconfirmed_approval_blocks_synthesis_and_missing_timeline_keeps_result(
    production, live_api, page
):
    page, expect = page
    _, script = production.approved("ux-read-errors")
    page.goto(WEB_URL + f"/audio/{script['id']}", wait_until="domcontentloaded")
    generate = page.get_by_role("button", name="Generate audio", exact=True)
    expect(generate).to_be_enabled()
    failing = [True]

    def unavailable_approval(route):
        if failing[0]:
            route.abort("failed")
        else:
            route.continue_()

    page.route(API_URL + f"/scripts/{script['id']}/approvals", unavailable_approval)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(page.get_by_role("alert")).to_contain_text("Status cannot be confirmed")
    expect(generate).to_be_disabled()
    expect(page.locator(".audio-production h2")).to_be_visible()
    failing[0] = False
    page.get_by_role("button", name="Refresh status", exact=True).click()
    expect(generate).to_be_enabled()
    page.route(
        re.compile(re.escape(API_URL) + r"/jobs/[^/]+/events"),
        lambda route: route.fulfill(
            status=503,
            headers={"Access-Control-Allow-Origin": WEB_URL},
            json={
                "code": "E2E_TIMELINE",
                "message": "Timeline unavailable",
                "details": {},
                "request_id": "e2e",
            },
        ),
    )
    generate.click()
    expect(page.locator(".audio-item")).to_have_count(1, timeout=20000)
    expect(
        page.get_by_text("Timeline unavailable. Your audio task is still saved.")
    ).to_be_visible()
    jobs = live_api.get(f"/jobs?script_id={script['id']}&type=audio_generation").json()
    assert jobs["total"] == 1
    assert jobs["items"][0]["status"] == "succeeded"
    # A second tab can supersede this script after the page's approval check.
    edited = live_api.patch(
        f"/scripts/{script['id']}",
        json={
            "text": script["text"] + "\nA newer external edit.",
            "expected_version": script["version"],
        },
    )
    edited.raise_for_status()
    expect(generate).to_be_enabled()
    generate.click()
    expect(page.locator(".audio-production .source-tag")).to_have_text("Superseded")
    expect(generate).to_be_disabled()
    assert (
        live_api.get(f"/jobs?script_id={script['id']}&type=audio_generation").json()[
            "total"
        ]
        == 1
    )
