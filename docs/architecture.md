# Architecture and development

[Project overview](../README.md) · [Operations](operations.md)

EchoFlow is a modular monolith: the API and background processes share business services, models and PostgreSQL. The [README](../README.md#how-it-is-built) shows the runtime architecture.

## Code organization

| Location | Responsibility |
| --- | --- |
| `backend/app/api` | HTTP validation, responses and simple read queries |
| `backend/app/services` | Business rules, transactions and production workflows |
| `backend/app/integrations` | Article fetching, LLM, TTS, storage and checkpoints |
| `backend/app/tasks` | Celery tasks, outbox dispatcher and maintenance loops |
| `backend/app/evaluation` | Deterministic checks and evidence positioning |
| `backend/app/models`, `schemas`, `db`, `core` | Persistence, contracts, sessions and shared configuration |
| `backend/alembic` | Database migrations |
| `frontend/src/pages`, `components`, `styles` | Pages, shared navigation/UI and styling |
| `frontend/src/api`, `domain`, `hooks` | API contracts, shared UI rules and cancellable reads/polling |
| `backend/tests/unit`, `integration`, `e2e` | Isolated logic, service/API/database checks and Compose acceptance |
| `frontend/tests` | Vitest tests for shared frontend logic |

## Dependency boundaries

`API / task entrypoints → services → models and integrations`

- Services own transactions; lower layers do not import HTTP routes or task entrypoints.
- PostgreSQL owns job state. Outbox delivery is at least once; atomic claims, execution leases and result constraints prevent stale workers or duplicate messages from creating duplicate results.
- Transactions lock Article → Job → outbox consistently. Preserve fencing checks and never blindly repeat uncertain provider calls.
- Editing creates immutable versions; approval applies to the latest version and evaluation. New synthesis rechecks eligibility; checkpoint recovery can retain the original approval.
- Celery task names are persisted in outbox/Redis messages. Renaming them requires compatibility with queued work.

## Tests

From the repository root, using your Python virtual environment (CI and containers use Python 3.12):

```sh
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend -q
python -m ruff check backend
python -m ruff format --check backend/app backend/tests
```

Default tests use SQLite, fake storage and Mock providers. Set `TEST_POSTGRES_URL` to a local PostgreSQL connection with CREATEDB permission to enable migration/concurrency checks; they create and drop only randomly named test databases, leaving the named database unchanged.

After building the frontend image, no local Node installation is needed:

```sh
docker compose run --rm --no-deps frontend npm test
docker compose run --rm --no-deps frontend npm run build
```

## Browser acceptance

With Docker running, install the optional dependencies and launch the isolated suite from PowerShell:

```powershell
python -m pip install -r backend/requirements-e2e.txt
python -m playwright install chromium
./scripts/run-e2e.ps1 -Suite all -Python python
```

Use `-Suite smoke` for production/browser flows or `-Suite recovery` for worker termination and upload recovery. The runner uses the fixed `echoflow-e2e` project, ports 15173/18000/15432, blank keys and separate volumes. It removes only its own test resources; screenshots and results go to ignored `.tools/e2e-results-*` directories. Automated tests do not call paid providers.

## CI

[GitHub Actions](../.github/workflows/ci.yml) runs on pushes and pull requests: backend lint/format and unit/integration tests with PostgreSQL, plus frontend tests and the TypeScript/Vite build. Enable `run_e2e` in a manual workflow run for the full Mock Compose suite. CI uses blank provider keys and does not deploy the application.
