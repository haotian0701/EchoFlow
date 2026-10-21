# EchoFlow

EchoFlow is an AI content-to-audio platform. This repository implements **stages 1-5: article management, asynchronous script generation, quality evaluation, human approval, MP3 audio delivery, safe recovery, and operational monitoring**.

## Run locally

1. Start Docker Desktop or another Docker engine.
2. Optionally copy `.env.example` to `.env` and change the local database password. If you change it, update `DATABASE_URL` to match.
3. Run `docker compose up --build -d` from the repository root.
4. Open <http://localhost:5173/>. The API documentation is at <http://localhost:8000/docs>.

Compose starts PostgreSQL, Redis and MinIO, runs Alembic migrations and private bucket initialization, then starts the API, outbox dispatcher, Celery worker, independent maintenance service, and frontend. PostgreSQL uses `postgres_data`, Redis uses `redis_data`, and MinIO uses `minio_data`; `docker compose down` retains these volumes and the `audio_staging` checkpoint volume. `docker compose down -v` removes their data.

MinIO is built from the pinned [official source release RELEASE.2025-10-15T17-29-55Z](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z) because the upstream prebuilt images are unavailable. The first build downloads Go and compiles MinIO, so it takes longer; subsequent builds use Docker's cache. Its console is at <http://localhost:9001>, with `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` from `.env` (development defaults: `echoflow` / `local-dev-password`). The S3 port stays internal and the audio bucket is private.

The frontend and API are bound to localhost for this single-user prototype. Frontend dependency installation and its production build happen inside the frontend image; local Node/npm is not required.

## Script generation

The default `LLM_PROVIDER=mock` runs the entire asynchronous workflow without a key or external charge. Select an article, choose target language, length, style, and audience, then click **Generate script**. Job progress and immutable script versions are persisted and restored after refresh.

To use OpenRouter, create `.env` from `.env.example` and set:

```dotenv
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=z-ai/glm-5.3-flash
OPENROUTER_EVALUATION_MODEL=z-ai/glm-5.3-flash
```

Restart the affected services with `docker compose up --build -d`. OpenRouter is never selected implicitly and the application never falls back to Mock when real mode fails. Keys remain in server-side environment variables and are not written to the database or logs.

Generation jobs move through `queued`, `running`, `retry_scheduled`, and a terminal state of `succeeded`, `failed`, or `needs_attention`. Temporary connection, rate-limit, and availability failures receive up to three attempts. An uncertain response timeout becomes `needs_attention` because repeating it could create another billed call. Open Job Monitor to review the call and create a linked manual retry.

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

Repeated requests with the same script, provider, model, voice, language, speed and format reuse an active job or an existing available recording. A failed/uncertain job or deleted recording can be replaced using a new idempotency key. Connection failures and HTTP 429/500/502/503/504 receive up to three attempts (5s and 30s delays). Read timeouts or interrupted responses become `needs_attention`; generating again may incur another charge. Storage upload failures retry only the upload, never synthesis. A durable audio checkpoint lets Stage 5 resume uploads after worker termination, without another synthesis call.

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

The optional `backend/tests/integration/postgres/` checks use `TEST_POSTGRES_URL` (a local PostgreSQL admin URL with CREATEDB permission). They create randomly named temporary databases, test existing data upgrades, concurrent task/audio operations and database constraints, then drop only those temporary databases. They never migrate or drop the database named in the supplied URL. These checks are skipped by default.

The backend is organized into `api`, `services`, `integrations`, `evaluation`, `models`, `schemas`, `db`, `core` and `tasks`. The frontend separates pages, shared components, API contracts/client and styles. See [Repository structure and test commands](docs/architecture.md) for dependency rules, entrypoints and test ownership.

Full Mock acceptance is now versioned in `backend/tests/e2e/`. Install `backend/requirements-e2e.txt` and Chromium, then run `./scripts/run-e2e.ps1 -Suite all` for production/browser/crash-recovery checks. The runner uses a separate `echoflow-e2e` Compose project, blank keys and independent volumes, and cleans up only its test resources.

### GitHub Actions

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on pushes and pull requests. Its parallel checks use Python 3.12 and Node 24, matching the application containers:

- **Backend checks:** Ruff lint/format checks and unit/integration tests, including a disposable PostgreSQL 17 service for migration, concurrency and constraint tests. The JUnit report is retained for seven days.
- **Frontend build:** `npm ci` followed by TypeScript checking and the Vite production build. The `frontend-dist` artifact is retained for seven days.
- **Full Mock E2E (manual):** in **Actions → CI → Run workflow**, enable `run_e2e` to also build/start the isolated Compose project and run production, browser and crash-recovery tests. Screenshots and downloaded test audio are retained for seven days.

Push this workflow with the source code to the GitHub repository; no repository-specific URL or Provider secrets are required. CI explicitly uses Mock providers and blank API keys. The workflow checks/builds the application and does not deploy it. Once the workflow exists on the default branch, the manual run option is available; branch protection can require **Backend checks** and **Frontend build**.



## Stage 5: deployment and recovery

When upgrading an existing Stage 4 database, stop the old task processes **before migration**:

```powershell
docker compose stop worker dispatcher maintenance
docker compose build
docker compose run --rm migrate
docker compose up -d --no-build
```

On a first install without a maintenance container, use `docker compose stop worker dispatcher` instead. Migration `0005_recovery` preserves terminal jobs and conservatively marks legacy `running` jobs and their evaluations `needs_attention`; their unfinished provider calls become `unknown`. Existing `.env` values stay authoritative. Environment changes require container recreation (`docker compose up -d --force-recreate api worker`), not only `restart`.

The database owns execution state. Each claim creates a `job_executions` row, renews its 60-second lease every 10 seconds, and fences stage/result commits with its execution ID. Maintenance checks leases every 15 seconds independently of Redis. A task lost before any provider call is prepared can be safely requeued within its three-attempt allowance. A prepared call with an unknown outcome, or an LLM result that could not be saved, requires human attention. The dispatcher commits `queued` before publishing and republishes unclaimed dispatched jobs after 120 seconds using the same outbox row. Duplicate deliveries cannot create duplicate results.

Audio synthesis first reserves a stable asset ID/object key, writes and validates the MP3, fsyncs data, and atomically commits a JSON checkpoint on `audio_staging`. Only Worker and maintenance mount this private volume. Checkpoints include hashes, sizes, duration, the original approval and provider-call identity, and available accounting metadata. Upload/database failures retain the file and reuse that object key. Recovery can save already synthesized audio against its original valid approval even after a later edit. A **new synthesis call** must satisfy current approval and voice requirements.

**Recovery boundary:** if external synthesis succeeded but the full checkpoint was not committed before interruption, the job becomes `needs_attention`. The application cannot guarantee avoiding another charge in this window. Complete recoverable audio is retained for seven days; Job Monitor shows its deadline. Expired/incomplete files and unreferenced objects older than 24 hours are cleaned only after reference/active-job checks. Storage cleanup runs independently of lease maintenance. Failed audio deletions are attempted at most three times automatically; the Audio Studio Delete action starts another explicit deletion attempt.

### Job Monitor and manual retry

Open <http://localhost:5173/jobs> or a task's **Details** link. Filter by article title, type, state and creation time. `/jobs/{job-id}` restores attempts, stage events, safe provider configuration, request IDs, usage, errors, result links and parent/root/child retry links after refresh. Lists poll every five seconds; active task details every two seconds. Polling stops on leaving the page, and task details stop when terminal.

Only `failed`/`needs_attention` jobs can create a retry. Each task has at most one direct retry child; retry that child if it fails again. The original task and its calls are retained. The UI distinguishes **Resume upload** (no new TTS call) from **Create retry job** (new provider call using current configuration). Unknown/billed-but-lost results require an explicit charge-risk acknowledgement. Obsolete script/evaluation inputs link back to the corresponding Studio for correction.

```http
POST /jobs/{job-id}/retry
Idempotency-Key: <uuid>
Content-Type: application/json

{"acknowledge_possible_charge": false}
```

A new child returns `202` and `Location`; replay or an existing direct child returns `200`. Conflicting request keys return `409 IDEMPOTENCY_CONFLICT`; unacknowledged charge risk returns `409 CHARGE_ACKNOWLEDGEMENT_REQUIRED`. Evaluation retries create a new report. Already active/equivalent audio returns a conflict with a reusable resource location. `/jobs/{id}/executions`, `/calls`, and `/events` are paginated (`items`, `total`, `limit`, `offset`).

### Dashboard and readiness

Open <http://localhost:5173/dashboard>. Choose 24 hours, seven days, 30 days, or all time. `GET /metrics/overview` accepts UTC `from`/`to` (inclusive start, exclusive end), defaults to seven days, and supports `all_time=true`. Job counts use job creation time, including manual children as separate jobs. Call usage uses call creation time and counts each call once. Current review workload is a point-in-time gauge; unresolved attention counts include only retry-chain leaves in the selected cohort.

Active duration is the sum of known execution durations per terminal job, excluding queue/backoff/human waiting. Jobs with abandoned/unknown-duration attempts are excluded, and sample coverage is shown. LLM/Judge/TTS latency reports measured call counts, separating Mock from real providers. Known OpenRouter USD, ElevenLabs credits, unknown-cost calls and Mock calls remain separate. They are never converted or summed into a fictional monetary total.

`GET /health/ready` checks PostgreSQL, Redis, the private MinIO bucket and a recovery heartbeat younger than 60 seconds. It returns `503` when a dependency is unavailable and never calls a paid provider. `/health` remains the basic database liveness check. Application JSON logs use whitelisted IDs and accounting metadata; raw provider errors, prompts, article/script bodies and credentials are excluded.

### Provider configuration

Default generation and Judge models are `z-ai/glm-5.3-flash`. Explicit `OPENROUTER_MODEL` and `OPENROUTER_EVALUATION_MODEL` in `.env` override defaults. New jobs store a whitelisted non-secret snapshot; already queued jobs keep their settings. Manual retries that call a provider capture the latest server configuration. API keys are always resolved at execution time and never stored in snapshots.

| Setting | Default |
| --- | --- |
| `GENERATION_TOKENS_SHORT` / `MEDIUM` / `LONG` | 4000 / 6000 / 8000 |
| `EVALUATION_TOKENS` | 4000 |
| `GENERATION_REASONING_EFFORT` / `EVALUATION_REASONING_EFFORT` | low / low |
| `GENERATION_TEMPERATURE` / `EVALUATION_TEMPERATURE` | 0.3 / 0.1 |

These settings are typed and bounded. Word-count targets, narration speed estimates and prompt versions remain business rules. Successful and invalid/truncated responses retain available provider request IDs, actual model, usage, cost and latency; missing values remain unknown.

### Idempotent Mock demonstration

Set **both** providers to `mock`, recreate API/Worker, then run:

```powershell
docker compose exec api python -m app.demo
```

The script checks both local configuration and the target API's mode before creating data, and refuses real-provider mode. It creates English/German samples and drives article -> asynchronous generation -> manual evaluation -> critical-issue acknowledgement/approval -> asynchronous audio using stable idempotency keys. Re-running reuses the same results. Demo approvals apply only to these clearly named demo samples. Mock audio is a two-second test tone.

### Recovery verification

Use a separate Mock Compose project and distinct volumes for fault injection. Verify temporary failures retry after 5/30 seconds; duplicate messages have one claimed execution; killing a Worker before a provider call safely requeues; killing during a prepared call requires attention; killing after a complete checkpoint resumes only upload. Stop MinIO after synthesis, restart Worker, restore MinIO, and confirm one synthesis call remains. Create a manual retry and inspect its preserved parent/call records. Finally run `docker compose down` / `up -d` (without `-v`) and verify jobs, events, retry links, metrics and downloads survive.

Backend tests use Mock adapters/HTTPX transports by default. PostgreSQL migration/concurrency tests use a randomly named disposable database when `TEST_POSTGRES_URL` is set (requires CREATEDB); they do not alter the named source database. Real OpenRouter/ElevenLabs smoke tests are manual and opt-in; never include keys or paid calls in automated acceptance.
