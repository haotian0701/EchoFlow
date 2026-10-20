import pytest

pytestmark = pytest.mark.e2e


def test_english_and_german_production_and_audio_delivery(production, live_api):
    for language in ("en", "de"):
        article, script = production.approved(language=language)
        job = production.wait(production.audio(script["id"], language)["id"])
        detail = live_api.get(f"/audio/{job['result_audio_asset_id']}").json()
        download = live_api.get(f"/audio/{detail['id']}/download")
        assert (
            download.status_code == 200 and len(download.content) == detail["file_size"]
        )
        partial = live_api.get(
            f"/audio/{detail['id']}/stream", headers={"Range": "bytes=0-127"}
        )
        assert partial.status_code == 206 and len(partial.content) == 128
        assert live_api.get(f"/jobs/{job['id']}/calls").json()["total"] == 1
        assert live_api.get(f"/jobs/{job['id']}/executions").json()["total"] == 1
        assert live_api.get(f"/jobs/{job['id']}/events").json()["total"] >= 3
        assert (
            live_api.get("/jobs", params={"article_id": article["id"]}).json()["total"]
            == 3
        )
    metrics = live_api.get("/metrics/overview?all_time=true").json()
    assert metrics["usage"]["mock_calls"] >= 6
    assert (
        metrics["usage"]["openrouter_usd"]
        == metrics["usage"]["elevenlabs_credits"]
        == 0
    )
