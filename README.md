# EchoFlow

EchoFlow is an AI content-to-audio platform. This repository currently implements **stage 1: article management**. Script generation, review, and audio production are described in [the technical plan](spec/technical-plan.md) and are not part of this stage.

## Run locally

1. Start Docker Desktop or another Docker engine.
2. Optionally copy `.env.example` to `.env` and change the local database password. If you change it, update `DATABASE_URL` to match.
3. Run `docker compose up --build -d` from the repository root.
4. Open <http://localhost:5173/>. The API documentation is at <http://localhost:8000/docs>.

Compose starts PostgreSQL, runs the Alembic migration, then starts the API and frontend. PostgreSQL uses the `postgres_data` named volume, so `docker compose down` and subsequent `up` calls retain saved articles and import records. `docker compose down -v` removes that volume and its data.

The frontend and API are bound to localhost for this single-user prototype. There are no API keys or external AI services in stage 1. Frontend dependency installation and its production build happen inside the frontend image; local Node/npm is not required.

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

New articles return `201`; duplicate content returns `200` with `duplicate: true`. Errors use `code`, `message`, `details`, and `request_id`; `X-Request-ID` is also returned as a header.

## Checks

With Python dependencies installed from `backend/requirements-dev.txt`, run `cd backend && pytest -q` and `ruff check .`. The tests use an in-memory SQLite database and mocked HTTP responses to exercise the API, duplicate handling, URL restrictions, failures, and size limits. Docker Compose is the intended PostgreSQL integration environment.

