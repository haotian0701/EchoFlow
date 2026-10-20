import os
import time

import httpx
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("ECHOFLOW_E2E_RECOVERY") != "1",
        reason="Requires the fault-injection worker",
    ),
]


def test_worker_crashes_and_storage_recovery(production, live_api, compose):
    def restart_worker():
        compose("kill", "-s", "SIGKILL", "worker")
        compose("up", "-d", "--no-deps", "--no-build", "worker")

    _, job = production.generate("e2e-before-call")
    production.wait(
        job["id"],
        lambda current: (
            current["stage"] == "preparing" and current["attempt_count"] == 1
        ),
    )
    restart_worker()
    assert production.wait(job["id"])["attempt_count"] == 2
    assert live_api.get(f"/jobs/{job['id']}/calls").json()["total"] == 1

    _, unknown = production.generate("e2e-during-call")
    production.wait(unknown["id"], lambda current: current["stage"] == "provider_call")
    restart_worker()
    production.wait(
        unknown["id"], lambda current: current["status"] == "needs_attention"
    )
    rejected = live_api.post(f"/jobs/{unknown['id']}/retry", json={})
    assert (
        rejected.status_code == 409
        and rejected.json()["code"] == "CHARGE_ACKNOWLEDGEMENT_REQUIRED"
    )
    child = production.post(
        f"/jobs/{unknown['id']}/retry", {"acknowledge_possible_charge": True}
    )["job"]
    production.wait(child["id"])

    _, temporary = production.generate("e2e-transient")
    assert production.wait(temporary["id"])["attempt_count"] == 2
    assert live_api.get(f"/jobs/{temporary['id']}/calls").json()["total"] == 2

    _, script = production.approved("e2e-after-checkpoint")
    audio = production.audio(script["id"])
    production.wait(
        audio["id"],
        lambda current: (
            current["stage"] == "uploading" and current["attempt_count"] == 1
        ),
    )
    restart_worker()
    production.wait(audio["id"])
    assert live_api.get(f"/jobs/{audio['id']}/calls").json()["total"] == 1

    _, script = production.approved("e2e-storage")
    compose("stop", "minio")
    try:
        audio = production.audio(script["id"])
        production.wait(
            audio["id"], lambda current: current["status"] == "needs_attention"
        )
        ready = live_api.get("/health/ready")
        assert ready.status_code == 503 and ready.json()["checks"]["maintenance"]
        restart_worker()
    finally:
        compose("start", "minio")
    # A complete checkpoint is recovered by the next maintenance pass, rather
    # than immediately when storage returns. Keep observing needs_attention.
    saved = production.wait(
        audio["id"], lambda current: current["status"] == "succeeded"
    )
    assert live_api.get(f"/jobs/{audio['id']}/calls").json()["total"] == 1
    assert (
        live_api.get(f"/audio/{saved['result_audio_asset_id']}/download").status_code
        == 200
    )

    before = live_api.get("/metrics/overview?all_time=true").json()
    compose("down")
    compose("up", "-d", "--no-build")
    for _ in range(40):
        try:
            if live_api.get("/health/ready").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(1)
            continue
        time.sleep(1)
    after = live_api.get("/metrics/overview?all_time=true").json()
    assert (
        before["total_jobs"] == after["total_jobs"]
        and before["usage"] == after["usage"]
    )
    assert (
        live_api.get(f"/jobs/{unknown['id']}").json()["retry_child_job_id"]
        == child["id"]
    )
    assert (
        live_api.get(f"/audio/{saved['result_audio_asset_id']}/download").status_code
        == 200
    )
