# EchoFlow

EchoFlow is an AI content-to-audio platform. This repository implements **stages 1–4: article management, asynchronous script generation, quality evaluation, human approval, and MP3 audio delivery**.

## Run locally

1. Start Docker Desktop or another Docker engine.
2. Optionally copy `.env.example` to `.env` and change the local database password. If you change it, update `DATABASE_URL` to match.
3. Run `docker compose up --build -d` from the repository root.
4. Open <http://localhost:5173/>. The API documentation is at <http://localhost:8000/docs>.

Compose starts PostgreSQL, Redis and MinIO, runs Alembic migrations and private bucket initialization, then starts the API, outbox dispatcher, Celery worker, and frontend. PostgreSQL uses `postgres_data`, Redis uses `redis_data`, and MinIO uses `minio_data`; `docker compose down` retains all three volumes. `docker compose down -v` removes their data.

MinIO is built from the pinned [official source release RELEASE.2025-10-15T17-29-55Z](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z) because the upstream prebuilt images are unavailable. The first build downloads Go and compiles MinIO, so it takes longer; subsequent builds use Docker's cache. Its console is at <http://localhost:9001>, with `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` from `.env` (development defaults: `echoflow` / `local-dev-password`). The S3 port stays internal and the audio bucket is private.

The frontend and API are bound to localhost for this single-user prototype. Frontend dependency installation and its production build happen inside the frontend image; local Node/npm is not required.

## Script generation

The default `LLM_PROVIDER=mock` runs the entire asynchronous workflow without a key or external charge. Select an article, choose target language, length, style, and audience, then click **Generate script**. Job progress and immutable script versions are persisted and restored after refresh.

To use OpenRouter, create `.env` from `.env.example` and set:

```dotenv
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=google/gemini-2.5-flash
OPENROUTER_EVALUATION_MODEL=google/gemini-2.5-flash
```

Restart the affected services with `docker compose up --build -d`. OpenRouter is never selected implicitly and the application never falls back to Mock when real mode fails. Keys remain in server-side environment variables and are not written to the database or logs.

Generation jobs move through `queued`, `running`, `retry_scheduled`, and a terminal state of `succeeded`, `failed`, or `needs_attention`. Temporary connection, rate-limit, and availability failures receive up to three attempts. An uncertain response timeout becomes `needs_attention` because repeating it could create another billed call. Stage 2 displays that outcome; full manual recovery arrives in a later stage.

## Evaluation and review

Open a saved script with **Review** to use the dedicated `/review/{script-id}` workspace. Evaluation is deliberately manual: generating or editing a script leaves it in `Pending evaluation` until **Evaluate** is clicked. The asynchronous evaluation combines deterministic checks for length, narration format, sentence length, and numbers with an LLM Judge report for factual consistency, content coverage, and audio readiness.

In Mock mode, `mock/evaluator-v1` produces stable scores from the deterministic findings and never calls an external service. In OpenRouter mode, the Judge uses `OPENROUTER_EVALUATION_MODEL`; it can differ from the script generation model. Scores are estimates and never approve a script automatically.

Editing creates a new immutable script version and does not copy evaluations or approvals. Only the latest version can be edited or approved. Approval requires the latest evaluation attempt to have succeeded, and every critical issue must be explicitly acknowledged. Starting another evaluation preserves earlier approvals as audit history but makes them stale until the new report is approved.

## Content Studio

- **Paste text:** enter a title, source, original language (English or German), and 100–30,000 characters of article text.
- **Import URL:** enter a public static HTML article URL. The title and source can be overridden; otherwise they come from the page and its host. Login-only and JavaScript-rendered pages should be pasted manually.
- **Library:** search saved titles, inspect the full original text, and review URL import history. Repeated normalized content returns the existing article without creating another row.

The URL importer allows HTTP/HTTPS on standard ports, checks every redirect target against public IP addresses, pins the connection to the checked IP, and limits redirects, time, and response size. Failed fetches and extractions are saved as import attempts with a readable error.

## Audio Studio

Open an approved script in Script Review and choose **Produce audio** to visit `/audio/{script-id}`. Choose a voice and speed (0.7–1.2×, default 1.0), then **Generate audio**. The language follows the script. Only the article's latest script, approved against its latest successful evaluation, can start production. The worker rechecks that approval before synthesis. Later edits or evaluations do not alter existing recordings or their approval references.

The default `TTS_PROVIDER=mock` is independent of `LLM_PROVIDER`. It creates a **two-second demonstration tone**, not a reading of the script, without calling an external service. Real narration requires an ElevenLabs key:

```dotenv
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=your-key
ELEVENLABS_MODEL=eleven_flash_v2_5
```

Apply `.env` changes with `docker compose up -d --force-recreate api worker`. A plain `docker compose restart` does not reload environment variables. Keys are sent only to API/worker containers. Real mode never falls back to Mock.

Stage 4 supports `eleven_flash_v2_5` only; its 40,000-character request limit covers the application's 30,000-character script limit. Other model IDs are rejected until their input limits and capabilities are implemented. Output is `mp3_44100_128`. Responses have a configurable 300-second total timeout and 64 MiB limit. The server queries the account's voices and caches them for 300 seconds; voices support both English and German through the multilingual model, while verified accents/languages are separate metadata. Samples are played only on demand.

`/audio` lists recordings, searches article titles, and provides playback, MP3 download, source script and job details. Playback supports HTTP byte ranges through the API; no browser needs a container-only address. Deletion removes the stored file and hides the recording from the library, while retaining metadata, approval references and job history. Failed deletion can be retried. Deleted audio metadata remains queryable by ID; its media endpoints return `410`.

Repeated requests with the same script, provider, model, voice, language, speed and format reuse an active job or an existing available recording. A failed/uncertain job or deleted recording can be replaced using a new idempotency key. Connection failures and HTTP 429/500/502/503/504 receive up to three attempts (5s and 30s delays). Read timeouts or interrupted responses become `needs_attention`; generating again may incur another charge. Storage upload failures retry only the upload, never synthesis. Forced worker termination recovery, general manual retries and scheduled orphan cleanup arrive in Stage 5.

ElevenLabs `character-cost` is saved as provider credits, **not dollars**; monetary cost is left unset when unavailable. Request IDs, input characters, latency and audio duration are persisted without storing script text in provider-call records.

### Manual audio verification

1. In Mock LLM and TTS modes, create an article, generate a script, evaluate it, acknowledge critical issues and approve it.
2. Choose **Produce audio**, generate, and observe `queued → running → succeeded`. The two-second tone must play, and **Download MP3** must deliver a playable file.
3. Submit the same configuration again; the existing recording must be reused. Change speed to create another recording.
4. Refresh `/audio/{script-id}` and `/audio`; job progress and recordings must reload. Use `docker compose down` followed by `docker compose up -d` and verify them again.
5. Delete one recording and confirm it disappears from the library; `GET /audio/{id}` retains the audit record and `/stream` returns `410`.
6. Optional paid smoke test: configure ElevenLabs, use one controlled short approved script, and generate once. Verify actual narration and the provider-call request ID/credits. Automated tests never perform paid calls.

## API

| Endpoint | Purpose |
|---|---|
| `POST /articles` | Create or reuse a pasted article |
| `POST /articles/import` | Import or reuse a public HTML article |
| `GET /articles` | Paginated summaries, optional `q` title filter |
| `GET /articles/{id}` | Full article |
| `GET /articles/imports` | Paginated import history |
| `GET /health` | Database-backed health check |
| `POST /jobs` | Queue script generation; supports `Idempotency-Key` |
| `GET /jobs` | Query persisted jobs by article or status |
| `GET /jobs/{id}` | Get current job state and result reference |
| `GET /jobs/{id}/events` | Get the job progress timeline |
| `GET /articles/{id}/scripts` | List immutable script versions |
| `GET /scripts/{id}` | Get a complete script version |
| `POST /scripts/{id}/regenerate` | Queue a new version from an existing script |
| `PATCH /scripts/{id}` | Create an edited immutable script version |
| `POST /scripts/{id}/evaluate` | Queue a manual quality evaluation |
| `GET /scripts/{id}/evaluations` | List evaluation history |
| `GET /evaluations/{id}` | Get a complete quality report |
| `POST /scripts/{id}/approve` | Approve the latest evaluated version |
| `GET /scripts/{id}/approvals` | List approval history |
| `GET /tts/voices?language=en` | Current provider voices and cache indicator |
| `POST /scripts/{id}/audio` | Queue/reuse audio; accepts language, voice_id, speed and Idempotency-Key |
| `GET /audio` | Paginated audio; q, article_id, script_id and status filters |
| `GET /audio/{id}` | Metadata, approval reference, article and generating job |
| `GET /audio/{id}/stream` | Private MP3 playback, including single HTTP byte ranges |
| `GET /audio/{id}/download` | MP3 attachment with a safe filename |
| `DELETE /audio/{id}` | Delete file and retain audit metadata; safe to repeat |

New articles return `201`; duplicate content returns `200` with `duplicate: true`. New asynchronous jobs return `202`; an idempotent replay returns the original Job. Errors use `code`, `message`, `details`, and `request_id`; `X-Request-ID` is also returned as a header.

## Checks

With Python dependencies installed from `backend/requirements-dev.txt`, run `cd backend && pytest -q` and `ruff check .`. Tests use SQLite, fake storage and mocked HTTP responses; no automated test calls OpenRouter or ElevenLabs. Docker Compose is the PostgreSQL, Redis, MinIO, dispatcher, and Celery integration environment. Audio tests include approval invalidation, idempotent reuse, provider accounting, Range responses, soft deletion and upload failure without repeated synthesis.

The optional `tests/test_audio_postgres.py` check uses `TEST_POSTGRES_URL` (a local PostgreSQL admin URL with CREATEDB permission). It creates a randomly named temporary database, upgrades Stage 3 data, checks concurrent creation and delivery plus foreign keys/uniqueness, then drops only that temporary database. It never migrates or drops the database named in the supplied URL. This check is skipped by default.

