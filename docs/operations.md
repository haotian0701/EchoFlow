# Configuration and recovery

[Project overview](../README.md) · [Architecture and tests](architecture.md)

Run commands from the repository root. Initial setup is in the [README](../README.md#try-it-locally); all supported settings are in [.env.example](../.env.example).

## Provider configuration

A fresh checkout uses Mock providers without API keys. To enable real services, copy `.env.example` to `.env` and set:

```dotenv
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key

TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=your-key
ELEVENLABS_MODEL=eleven_flash_v2_5
```

Generation and evaluation models can be changed with `OPENROUTER_MODEL` and `OPENROUTER_EVALUATION_MODEL`. The current TTS implementation supports `eleven_flash_v2_5` only. Mock audio is a two-second test tone, not narration.

Apply environment changes by recreating API and Worker:

```sh
docker compose up -d --force-recreate api worker
```

A plain restart does not reload `.env`. Keys remain server-side; real mode never silently falls back to Mock. New jobs capture provider settings; existing jobs retain their configuration snapshots.

## Common commands and data

| Task | Command |
| --- | --- |
| Start or rebuild the application | `docker compose up --build -d` |
| Update only the frontend | `docker compose up -d --build --no-deps frontend` |
| Inspect task logs | `docker compose logs --tail=100 worker dispatcher maintenance` |
| Stop containers and keep data | `docker compose down` |

Named volumes persist PostgreSQL, Redis, MinIO and audio checkpoints. **`docker compose down -v` deletes those volumes and their data.**

The UI is at <http://localhost:5173>, API docs at <http://localhost:8000/docs>, and MinIO console at <http://localhost:9001>. MinIO credentials use `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` from `.env` or the development defaults in `.env.example`. `/health/ready` checks the database, Redis, storage and maintenance heartbeat without calling a paid provider.

Before upgrading the database schema, stop task processes so old workers cannot write during migration:

```sh
docker compose stop worker dispatcher maintenance
docker compose build
docker compose run --rm migrate
docker compose up -d --no-build
```

## Job Monitor and manual retry

Open **Jobs** or a task's **Details** link to inspect errors, executions, provider calls and results.

- Known temporary failures retry automatically, up to three attempts with 5/30-second delays.
- `failed` and `needs_attention` tasks can create a linked retry; the original records remain. Each task has at most one direct retry child.
- **Resume upload** reuses complete saved audio without another synthesis call. A new provider call may need an explicit charge-risk acknowledgement; new synthesis must also pass current version/approval checks.
- A complete audio checkpoint remains recoverable for seven days. If synthesis completed but the checkpoint was not saved, the outcome is uncertain; recovery cannot guarantee avoiding another charge.

Dashboard keeps Mock calls, known OpenRouter USD, ElevenLabs credits and unknown costs separate. Missing cost information is not treated as zero.

## Mock demonstration

Set both providers to `mock`, recreate API/Worker, then run:

```sh
docker compose exec api python -m app.demo
```

The demo refuses real-provider mode. It creates English/German samples and completes generation, evaluation, sample approval and audio production. Re-running reuses its results. For isolated fault-injection checks, see [browser acceptance](architecture.md#browser-acceptance).
