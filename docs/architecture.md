# Repository structure

EchoFlow is a modular monolith. HTTP requests and asynchronous processes share the same business services and database models. A change should go into the package that owns its responsibility; adding a forwarding layer for every database operation is unnecessary.

```text
backend/
  app/
    main.py                  FastAPI composition and middleware
    demo.py                  Idempotent Mock demonstration CLI
    core/                    Settings, errors, logging, time, fingerprints, script rules
    db/                      Engine, session factory and request dependency
    models/                  SQLAlchemy tables grouped by articles/scripts/jobs/reviews/audio
    schemas/                 API validation and serialization grouped by business area
    api/                     Routes, HTTP status/headers and response presenters
    services/                Business operations, transactions and production workflows
    integrations/            Article fetching, OpenRouter, ElevenLabs, MinIO, checkpoints
    evaluation/              Judge contracts, deterministic rules and evidence positioning
    tasks/                   Celery registration and dispatcher/maintenance process entrypoints
  alembic/                   Existing migration history
  tests/
    conftest.py              Safe Mock settings, explicit sessions/client fixtures
    database.py              Disposable SQLite setup
    factories.py             Test-data builders
    fakes.py                 In-memory providers and storage
    unit/                    Rules, adapters with MockTransport, settings, architecture
    integration/
      api/                   HTTP behavior, review constraints and metrics
      jobs/                  Generation/evaluation, leases, outbox, retries, accounting
      audio/                 Production, durable checkpoints and cleanup
      postgres/              Migration, concurrency and database constraint checks
    e2e/                     Opt-in real Compose flow, browser and crash recovery
frontend/src/
  App.tsx                    Route declarations
  main.tsx                   React bootstrap
  pages/                     Content, Review, Audio, Jobs and Dashboard pages
  components/                Shared visual components
  api/                       HTTP client and separate TypeScript contracts
    domain/                    Shared review states, navigation, evidence offsets and request keys
    hooks/                     Cancellable resource reads and sequential task polling
  styles/                    Shared and page-specific styles
scripts/
  run-e2e.ps1                Isolated Mock Compose acceptance runner
```

## Dependency boundaries

```text
HTTP routes / task entrypoints
              |
              v
         business services ----> integrations
              |                      |
              v                      v
        models / schemas           core
```

- `api` validates HTTP inputs and handles status codes, headers, streaming, and response presentation. Simple read queries can stay in routes; complex aggregation and write transactions belong in services.
- `tasks` registers messages and owns long-running process loops. Worker, maintenance and API share services; services do not import Celery or task entrypoints.
- `services` owns transaction boundaries and the Article → Job → outbox lock order. Moving code must not introduce intermediate commits, change fencing checks, or repeat uncertain provider calls.
- `integrations` contains external protocols and durable-file operations. It uses the shared `core.provider_errors.ProviderError`, so TTS and recovery do not depend on the generation adapter.
- `evaluation.rules` and `evaluation.evidence` contain the deterministic checks. HTTP calls and Mock/OpenRouter Judge implementations live in `integrations.llm.evaluation`.
- `models.__init__` registers all models. Alembic imports Base through this package, preserving complete metadata after the model split. Splitting Python modules does not require a schema migration.
- `schemas` keeps validation contracts separate from persistence. Services can accept validated request/config objects without depending on FastAPI.

`tests/unit/test_architecture.py` checks that lower layers do not import HTTP/process entrypoints, all tables remain registered, and serialized task names remain compatible.

## Process and message compatibility

Compose uses these Python entrypoints:

| Process | Entrypoint |
| --- | --- |
| API | `uvicorn app.main:app` |
| Worker | `celery -A app.tasks.celery_app:celery_app worker` |
| Dispatcher | `python -m app.tasks.dispatcher` |
| Maintenance | `python -m app.tasks.maintenance` |
| Bucket initialization | `python -m app.integrations.storage` |
| Mock demo | `python -m app.demo` |

Persisted outbox rows and Redis messages still use `app.worker.generate_script_task`, `app.worker.evaluate_script_task`, and `app.audio_worker.generate_audio_task`. These are explicit Celery registration names; they intentionally remain unchanged even though the Python modules moved. HTTP paths and response fields also remain unchanged. Old private Python import paths are removed rather than retained as forwarding modules.

## Tests

Default tests use explicit `sessions` and `client` fixtures. Shared test data belongs in `factories.py`, and fake dependencies in `fakes.py`; test cases must not import other test-case modules or attach a database factory to an HTTP client object. The default settings fixture uses Mock providers, blank credentials, and a per-test staging directory.

From the repository root:

```powershell
.venv/Scripts/python.exe -m pytest backend -q
.venv/Scripts/python.exe -m ruff check backend/app backend/tests
```

Frontend pure-logic tests live in `frontend/tests/` and use Vitest's Node environment. Run `npm test` in `frontend`, or `docker compose run --rm --no-deps frontend npm test` after building. CI runs these tests alongside TypeScript/production checks. Browser interaction coverage remains in the opt-in Mock E2E suite.

PostgreSQL checks are separated into migration, task concurrency, and audio concurrency/constraints. Set `TEST_POSTGRES_URL` to a local admin connection with CREATEDB permission, then run:

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/integration/postgres -q
```

Each test creates and drops only its own randomly named `echoflow_test_*` database. The database named in the supplied connection is used for administration and is never migrated or dropped. SQLite tests do not replace PostgreSQL locking/concurrency checks.

## Compose and browser acceptance

Install the optional runner dependencies and Chromium:

```powershell
.venv/Scripts/python.exe -m pip install -r backend/requirements-e2e.txt
.venv/Scripts/python.exe -m playwright install chromium
```

With Docker Desktop running, use:

```powershell
# English/German production and browser flows
./scripts/run-e2e.ps1 -Suite smoke

# Worker termination, upload-only recovery and restart persistence
./scripts/run-e2e.ps1 -Suite recovery

# Both suites
./scripts/run-e2e.ps1 -Suite all
```

The runner builds the images, starts the fixed `echoflow-e2e` project, runs tests and removes only that project's containers/volumes. API/UI/PostgreSQL use ports 18000/15173/15432. Provider keys are blank and both providers are explicitly Mock; tests check the live API mode before writing. Main application data and `.env` are unchanged.

The recovery override mounts only the E2E helper directory read-only and enables an explicitly guarded Mock Worker. Fault-injection code is excluded from the production image by the existing Docker ignore rules. Test results and screenshots use a unique ignored `.tools/e2e-results-*` directory; no source code or required test helper lives in `.tools`.

Default pytest discovery includes `unit` and `integration`. E2E tests are explicitly invoked by the runner; neither ordinary tests nor E2E execute paid-provider smoke calls.
