# Abnormal File Vault

Abnormal File Vault is a Django REST API for secure file storage with SHA-256 deduplication, parameterized search, sliding-window rate limiting, and per-user storage quotas. The project is structured as a single-container backend for the take-home challenge, with service boundaries that can later be extracted cleanly.

## Architecture Overview

- **Runtime:** Django REST Framework behind Gunicorn on port `8000`.
- **Identity:** API routes under `/api/` require a non-empty `UserId` header. Full authentication is intentionally out of scope for this challenge.
- **Persistence:** SQLite stores file metadata; Django `FileField` storage writes encrypted file bytes to the Docker media volume.
- **Service layer:** `DeduplicationService`, `EncryptionService`, and `FileQueryService` own business logic. Views remain thin orchestrators.
- **Operational controls:** Rate limits and quotas are configured in Django settings so thresholds can change without code changes.

## Prerequisites

- Docker Desktop or Docker Engine with Docker Compose v2.
- Python `3.10.19`, matching `.python-version`.
- `pip`, included with a normal Python install.

Confirm the tools are available:

```bash
docker --version
docker compose version
python --version
python -m pip --version
```

## Local Python Setup

Create and activate a virtual environment from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install local development and test dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

This installs Django, Django REST Framework, `requests`, `pytest`, and the other backend dependencies needed for local development and E2E test commands.

When you open a new terminal, reactivate the environment before running Python commands:

```bash
source .venv/bin/activate
```

## Docker Setup

Build and start the API:

```bash
docker compose up --build
```

This command rebuilds the backend image from `backend/Dockerfile` and starts the container from that image. It is usually enough for testing API changes because the Dockerfile copies the backend source code into the image.

Re-run it after changing API code, `backend/Dockerfile`, or `backend/requirements.txt`.

The API is available at:

```text
http://localhost:8000
```

Useful smoke check:

```bash
curl -H "UserId: local-dev" http://localhost:8000/api/files/
```

Requests to `/api/` without `UserId` return `401`; empty or whitespace-only values return `400`.

Stop the running container with `Ctrl+C`, or from another terminal:

```bash
docker compose down
```

## Fresh Data Reset

The Docker setup uses named volumes for SQLite data and uploaded media. Rebuilding the image does not erase those volumes.

To wipe local API data and start from an empty database/media store:

```bash
docker compose down -v
docker compose up --build
```

The `-v` flag deletes named Docker volumes, including the SQLite database and uploaded files. Use it only when you intentionally want a clean local state.

## E2E Progress Dashboard

Step 2 adds a requests-based E2E test client. These tests intentionally assert the final PRD contract, so they are expected to fail until the later build-plan steps are implemented.

Run the API in one terminal:

```bash
docker compose up --build
```

Run the dashboard from the repository root in a second terminal:

```bash
source .venv/bin/activate
python -m pytest tests/e2e -q
```

If you are already inside the `tests/` directory, run:

```bash
python -m pytest e2e -q
```

Override the API target when needed:

```bash
FILE_VAULT_BASE_URL=http://localhost:8000 python -m pytest tests/e2e -q
```

If the server is not running, the suite fails with a clear startup message instead of connection noise.

## Common Commands

| Task | Command |
| --- | --- |
| Install local dependencies | `python -m pip install -r backend/requirements.txt` |
| Start or rebuild the API | `docker compose up --build` |
| Stop containers | `docker compose down` |
| Reset local Docker data | `docker compose down -v` |
| Run E2E tests from repo root | `python -m pytest tests/e2e -q` |
| Run E2E tests from `tests/` | `python -m pytest e2e -q` |
| Smoke-test file list endpoint | `curl -H "UserId: local-dev" http://localhost:8000/api/files/` |

## API Service Map

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/files/` | `GET` | List files for the current `UserId`, with filtering and pagination. |
| `/api/files/` | `POST` | Upload a multipart `file` with hash-based deduplication. |
| `/api/files/{id}/` | `GET` | Retrieve metadata for a single file owned by the current `UserId`. |
| `/api/files/{id}/` | `DELETE` | Delete a file while preserving deduplication invariants. |
| `/api/files/{id}/download/` | `GET` | Download decrypted file content. |
| `/api/files/storage_stats/` | `GET` | Return storage usage and deduplication savings. |
| `/api/files/file_types/` | `GET` | Return distinct MIME types for the current `UserId`. |

## Build Plan Progress

- Step 1: Project setup and dependency baseline.
- Step 2: E2E client skeleton and README skeleton. The E2E suite is now the progress dashboard.
- Step 3: PRD file model and indexes.
- Step 4: API-scoped `UserIdMiddleware`, including 401/400 auth errors and per-user queryset scoping.
- Steps 5-13: Implement upload, encryption, deduplication, filtering, delete cascade, stats, throttling, quotas, and edge cases.
- Step 14: Finalize README with full API docs, configuration reference, and operational notes.

## Configuration Reference

The final implementation will expose these settings in `core/settings.py`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `RATE_LIMIT_CALLS` | `2` | Max API calls per user per window. |
| `RATE_LIMIT_PERIOD` | `1` | Sliding-window size in seconds. |
| `STORAGE_LIMIT_BYTES` | `10 * 1024 * 1024` | Per-user actual storage quota. |
| `MAX_UPLOAD_SIZE_BYTES` | `10 * 1024 * 1024` | Per-file upload limit. |
| `ENCRYPTION_KEY` | environment variable | Base64-encoded encryption key, never committed. |
| `DEFAULT_PAGE_SIZE` | `20` | Default list pagination size. |
| `MAX_PAGE_SIZE` | `100` | Maximum list pagination size. |
