# EchoFlow

EchoFlow is an AI content-to-audio platform. This repository currently implements **stages 1–2: article management and asynchronous script generation**. Evaluation, review, and audio production are planned next.

## Run locally

1. Start Docker Desktop or another Docker engine.
2. Optionally copy `.env.example` to `.env` and change the local database password. If you change it, update `DATABASE_URL` to match.
3. Run `docker compose up --build -d` from the repository root.
4. Open <http://localhost:5173/>. The API documentation is at <http://localhost:8000/docs>.

Compose starts PostgreSQL and Redis, runs the Alembic migrations, then starts the API, outbox dispatcher, Celery worker, and frontend. PostgreSQL uses the `postgres_data` named volume and Redis uses `redis_data`; `docker compose down` retains saved articles, jobs, events, and scripts. `docker compose down -v` removes both volumes and their data.

The frontend and API are bound to localhost for this single-user prototype. Frontend dependency installation and its production build happen inside the frontend image; local Node/npm is not required.

## Script generation

The default `LLM_PROVIDER=mock` runs the entire asynchronous workflow without a key or external charge. Select an article, choose target language, length, style, and audience, then click **Generate script**. Job progress and immutable script versions are persisted and restored after refresh.

To use OpenRouter, create `.env` from `.env.example` and set:

```dotenv
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=google/gemini-2.5-flash
```

Restart the affected services with `docker compose up --build -d`. OpenRouter is never selected implicitly and the application never falls back to Mock when real mode fails. Keys remain in server-side environment variables and are not written to the database or logs.

Generation jobs move through `queued`, `running`, `retry_scheduled`, and a terminal state of `succeeded`, `failed`, or `needs_attention`. Temporary connection, rate-limit, and availability failures receive up to three attempts. An uncertain response timeout becomes `needs_attention` because repeating it could create another billed call. Stage 2 displays that outcome; full manual recovery arrives in a later stage.

## Content Studio

- **Paste text:** enter a title, source, original language (English or German), and 100–30,000 characters of article text.
- **Import URL:** enter a public static HTML article URL. The title and source can be overridden; otherwise they come from the page and its host. Login-only and JavaScript-rendered pages should be pasted manually.
- **Library:** search saved titles, inspect the full original text, and review URL import history. Repeated normalized content returns the existing article without creating another row.

The URL importer allows HTTP/HTTPS on standard ports, checks every redirect target against public IP addresses, pins the connection to the checked IP, and limits redirects, time, and response size. Failed fetches and extractions are saved as import attempts with a readable error.

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

New articles return `201`; duplicate content returns `200` with `duplicate: true`. New asynchronous jobs return `202`; an idempotent replay returns the original Job. Errors use `code`, `message`, `details`, and `request_id`; `X-Request-ID` is also returned as a header.

## Checks

With Python dependencies installed from `backend/requirements-dev.txt`, run `cd backend && pytest -q` and `ruff check .`. Tests use SQLite, fake workers, and mocked HTTP responses; no automated test calls OpenRouter. Docker Compose is the PostgreSQL, Redis, dispatcher, and Celery integration environment.

