# Abnormal File Vault

Abnormal File Vault is a Django REST API for secure file storage with SHA-256 deduplication, parameterized search, sliding-window rate limiting, and per-user storage quotas. The project is structured as a single-container backend for the take-home challenge, with service boundaries that can later be extracted cleanly.

## Architecture Overview

- **Runtime:** Django REST Framework behind Gunicorn on port `8000`.
- **Identity:** Every API request uses a `UserId` header. Full authentication is intentionally out of scope for this challenge.
- **Persistence:** SQLite stores file metadata; Django `FileField` storage writes encrypted file bytes to the Docker media volume.
- **Service layer:** `DeduplicationService`, `EncryptionService`, and `FileQueryService` own business logic. Views remain thin orchestrators.
- **Operational controls:** Rate limits and quotas are configured in Django settings so thresholds can change without code changes.

## Local Setup

Build and start the API:

```bash
docker compose up --build
```

The API is available at:

```text
http://localhost:8000
```

Useful smoke check:

```bash
curl -H "UserId: local-dev" http://localhost:8000/api/files/
```

## E2E Progress Dashboard

Step 2 adds a requests-based E2E test client. These tests intentionally assert the final PRD contract, so they are expected to fail until the later build-plan steps are implemented.

Run the dashboard:

```bash
python -m pytest tests/e2e -q
```

Override the API target when needed:

```bash
FILE_VAULT_BASE_URL=http://localhost:8000 python -m pytest tests/e2e -q
```

If the server is not running, the suite fails with a clear startup message instead of connection noise.

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
- Steps 3-13: Implement model, auth middleware, upload, encryption, deduplication, filtering, delete cascade, stats, throttling, quotas, and edge cases.
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
