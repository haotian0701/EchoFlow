"""Idempotent demonstration through the real asynchronous HTTP workflow.

Run inside the API container: python -m app.demo. Refuses paid provider modes.
"""

import asyncio

import httpx

from app.core.config import get_settings


async def run(base_url="http://127.0.0.1:8000"):
    settings = get_settings()
    if settings.llm_provider != "mock" or settings.tts_provider != "mock":
        raise SystemExit(
            "Demo refused: both LLM_PROVIDER and TTS_PROVIDER must be mock."
        )
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        health = await client.get("/health")
        health.raise_for_status()
        if health.json().get("providers") != {"llm": "mock", "tts": "mock"}:
            raise SystemExit("Demo refused: the target API is not in fully Mock mode.")

        async def post(url, body=None, key=None):
            result = await client.post(
                url, json=body, headers={"Idempotency-Key": key} if key else {}
            )
            result.raise_for_status()
            return result.json()

        async def wait(job):
            for _ in range(180):
                response = await client.get(f"/jobs/{job['id']}")
                response.raise_for_status()
                latest = response.json()
                if latest["status"] == "succeeded":
                    return latest
                if latest["status"] in ("failed", "needs_attention"):
                    raise RuntimeError(
                        f"Demo task {latest['id']}: {latest['error_code']}"
                    )
                await asyncio.sleep(1)
            raise RuntimeError("Demo task did not finish within 180 seconds")

        results = []
        for language, title, sentence in (
            (
                "en",
                "EchoFlow Demo: Berlin research",
                "The Berlin research team published its report in 2026. The study involved 120 participants. The team plans to share its findings at a public meeting. ",
            ),
            (
                "de",
                "EchoFlow Demo: Forschung in Berlin",
                "Das Forschungsteam in Berlin veröffentlichte seinen Bericht im Jahr 2026. An der Studie nahmen 120 Personen teil. Das Team plant eine öffentliche Veranstaltung zu den Ergebnissen. ",
            ),
        ):
            article = (
                await post(
                    "/articles",
                    {
                        "title": title,
                        "text": sentence * 12,
                        "source": "EchoFlow Mock Demo",
                        "language": language,
                    },
                )
            )["article"]
            generated = await post(
                "/jobs",
                {
                    "type": "script_generation",
                    "article_id": article["id"],
                    "config": {
                        "target_language": language,
                        "length": "short",
                        "style": "news",
                        "audience": "general",
                    },
                },
                f"demo-v1-{language}-generate",
            )
            job = await wait(generated["job"])
            script_id = job["result_script_version_id"]
            evaluated = await post(
                f"/scripts/{script_id}/evaluate", key=f"demo-v1-{language}-evaluate"
            )
            evaluation_job = await wait(evaluated["job"])
            report = (
                await client.get(
                    f"/evaluations/{evaluation_job['result_evaluation_id']}"
                )
            ).json()
            script = (await client.get(f"/scripts/{script_id}")).json()
            await post(
                f"/scripts/{script_id}/approve",
                {
                    "evaluation_id": report["id"],
                    "expected_version": script["version"],
                    "acknowledged_critical_issue_ids": [
                        i["id"] for i in report["issues"] if i["severity"] == "critical"
                    ],
                },
            )
            audio = await post(
                f"/scripts/{script_id}/audio",
                {"language": language, "voice_id": "mock-chime", "speed": 1},
                f"demo-v1-{language}-audio",
            )
            finished = await wait(audio["job"])
            results.append(
                {
                    "language": language,
                    "article_id": article["id"],
                    "script_id": script_id,
                    "audio_id": finished["result_audio_asset_id"],
                }
            )
        print(results)
        return results


if __name__ == "__main__":
    asyncio.run(run())
