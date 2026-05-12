# Abnormal File Vault

Abnormal File Vault is a Django REST API for secure file storage with SHA-256 deduplication, parameterized search, sliding-window rate limiting, per-user storage quotas, encrypted storage at rest, and streaming downloads.

The project is intentionally packaged as a single-container backend for the take-home challenge. Internally, the code is split into clear service boundaries so deduplication, encryption, and query behavior can be extracted later without changing the public API.

## Companion documents

| Document | Purpose |
| --- | --- |
| [docs/AbnormalFileVault_PRD.docx](docs/AbnormalFileVault_PRD.docx) | Product requirements, API contract, assumptions, test strategy, and build plan. |
| [docs/AbnormalFileVault_Architecture.docx](docs/AbnormalFileVault_Architecture.docx) | Runtime architecture, module breakdown, data model, query/index design, and scale path. |
| [docs/Mermaid.md](docs/Mermaid.md) | Mermaid architecture and sequence diagrams for upload, deduplication, rate limiting, delete, search, stats, file types, and download flows. |

## Architecture overview

- **Runtime:** Django REST Framework behind Gunicorn on port `8000`; Docker Compose runs two workers by default.
- **Identity:** API routes under `/api/` require a non-empty `UserId` header. Full user registration/authentication is intentionally out of scope.
- **Persistence:** SQLite stores metadata in `/app/data/db.sqlite3`; Django `FileField` storage writes encrypted bytes to `/app/media`.
- **Cache:** Docker Compose starts Redis and sets `REDIS_URL` so rate limits use an atomic Lua/ZSET sliding window across Gunicorn workers. Local Python runs without `REDIS_URL` use LocMemCache.
- **Service layer:** `DeduplicationService`, `EncryptionService`, and `FileQueryService` own the business logic. Views orchestrate requests and responses.
- **Encryption:** New physical files are encrypted with chunked AES-GCM before storage. Downloads decrypt through `StreamingHttpResponse`.
- **Operational controls:** Rate limits, quotas, page sizes, and encryption chunk size are settings-driven.

## Prerequisites

- Docker Desktop or Docker Engine with Docker Compose v2.
- Python `3.10.19`, matching the Docker base image.
- `pip`, included with a normal Python install.

Confirm the tools are available:

```bash
docker --version
docker compose version
python --version
python -m pip --version
```

## Local Python setup

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

When you open a new terminal, reactivate the environment before running Python commands:

```bash
source .venv/bin/activate
```

## Docker setup

Build and start the API:

```bash
docker compose up --build
```

Docker Compose starts the backend plus an internal Redis service. The backend uses `GUNICORN_WORKERS=2` and `REDIS_URL=redis://redis:6379/0` so sliding-window rate limits are process-global through Redis sorted sets and an atomic Lua script. Redis is not exposed on a host port.

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

## Fresh data reset

Docker Compose uses named volumes for SQLite data, encrypted media, and static files. Rebuilding the image does not erase those volumes.

To wipe local API data and start from an empty database/media store:

```bash
docker compose down -v
docker compose up --build
```

The `-v` flag deletes named Docker volumes, including uploaded encrypted files. Use it only when you intentionally want a clean local state.

## Common commands

| Task | Command |
| --- | --- |
| Install local dependencies | `python -m pip install -r backend/requirements.txt` |
| Start or rebuild the API | `docker compose up --build` |
| Stop containers | `docker compose down` |
| Reset local Docker data | `docker compose down -v` |
| Run backend file tests | `.venv/bin/python -m pytest backend/files/tests -q` |
| Run E2E tests from repo root | `.venv/bin/python -m pytest tests/e2e -q` |
| Run sanity E2E tests | `.venv/bin/python -m pytest tests/e2e/test_sanity_check_files.py -q` |
| Smoke-test file list endpoint | `curl -H "UserId: local-dev" http://localhost:8000/api/files/` |

## Testing

Run the backend regression suite from the repository root:

```bash
.venv/bin/python -m pytest backend/files/tests -q
```

Run the API in one terminal before running E2E tests:

```bash
docker compose up --build
```

Then run the E2E suite in a second terminal:

```bash
.venv/bin/python -m pytest tests/e2e -q
```

Override the API target when needed:

```bash
FILE_VAULT_BASE_URL=http://localhost:8000 .venv/bin/python -m pytest tests/e2e -q
```

Optional sanity E2E tests upload and download the provided files under `tests/fixtures/sanity_check/` and write downloaded verification files into `tests/fixtures/sanity_check/downloads/`:

```bash
.venv/bin/python -m pytest tests/e2e/test_sanity_check_files.py -q
```

If the API server is not running, the E2E suites fail with a clear startup message instead of connection noise.

## API contract

All endpoints under `/api/` require:

```text
UserId: <non-empty string>
```

Common errors:

| Status | Body | Meaning |
| --- | --- | --- |
| `400` | `{"detail": "UserId must not be empty"}` | `UserId` was present but empty or whitespace-only. |
| `401` | `{"detail": "UserId header required"}` | Missing `UserId` header. |
| `404` | DRF not-found body | File does not exist or belongs to another user. |
| `429` | `{"detail": "Call Limit Reached"}` | More than `RATE_LIMIT_CALLS` requests inside the sliding window. |
| `429` | `{"detail": "Storage Quota Exceeded"}` | New physical upload would exceed the user's storage quota. |

### List files

```http
GET /api/files/
```

Returns a paginated envelope:

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "8f40cb41-6c8a-44d0-92c6-9eec4c7f89fd",
      "user_id": "local-dev",
      "original_filename": "sample.pdf",
      "file_type": "application/pdf",
      "size": 1234,
      "file_hash": "64-character-sha256-hex",
      "is_reference": false,
      "original_file": null,
      "reference_count": 1,
      "uploaded_at": "2026-05-12T00:00:00Z"
    }
  ]
}
```

Supported query parameters:

| Parameter | Behavior |
| --- | --- |
| `search` | Case-insensitive filename contains search. |
| `file_type` | Exact MIME type match. |
| `min_size` | Minimum size in bytes. |
| `max_size` | Maximum size in bytes. |
| `start_date` | Inclusive ISO-8601 upload timestamp lower bound. |
| `end_date` | Inclusive ISO-8601 upload timestamp upper bound. |
| `page` | Page number. |
| `page_size` | Page size, capped by `MAX_PAGE_SIZE`. |

Example:

```bash
curl -H "UserId: local-dev" \
  "http://localhost:8000/api/files/?search=report&file_type=application/pdf&page_size=10"
```

### Upload file

```http
POST /api/files/
Content-Type: multipart/form-data
```

The multipart form field must be named `file`.

Example:

```bash
curl -X POST \
  -H "UserId: local-dev" \
  -F "file=@tests/fixtures/sample.pdf" \
  http://localhost:8000/api/files/
```

Upload behavior:

- The file is streamed to a temporary upload handler before hashing/encryption.
- SHA-256 is computed from file bytes.
- MIME type is detected from file content using `python-magic`.
- Duplicate bytes for the same user create a reference row instead of writing another physical file.
- Duplicate uploads bypass quota because they add no new bytes on disk.
- Non-duplicate uploads are checked against `STORAGE_LIMIT_BYTES`, encrypted, and saved.

### Retrieve metadata

```http
GET /api/files/{id}/
```

Example:

```bash
curl -H "UserId: local-dev" \
  http://localhost:8000/api/files/8f40cb41-6c8a-44d0-92c6-9eec4c7f89fd/
```

Files are scoped to the requesting `UserId`. Requests for another user's file return `404`.

### Download file

```http
GET /api/files/{id}/download/
```

Example:

```bash
curl -L \
  -H "UserId: local-dev" \
  -o downloaded_sample.pdf \
  http://localhost:8000/api/files/8f40cb41-6c8a-44d0-92c6-9eec4c7f89fd/download/
```

The response streams decrypted plaintext bytes with `Content-Disposition: attachment`. Reference records download from the original encrypted storage object while preserving the reference's filename and content type.

### Delete file

```http
DELETE /api/files/{id}/
```

Example:

```bash
curl -X DELETE \
  -H "UserId: local-dev" \
  http://localhost:8000/api/files/8f40cb41-6c8a-44d0-92c6-9eec4c7f89fd/
```

Delete behavior:

- Deleting a reference decrements the original's `reference_count` and removes only the reference row.
- Deleting an original with references promotes the oldest reference to become the new original and retains the encrypted file.
- Deleting an original with no references deletes the encrypted file and frees quota.

### Storage stats

```http
GET /api/files/storage_stats/
```

Example:

```bash
curl -H "UserId: local-dev" \
  http://localhost:8000/api/files/storage_stats/
```

Response:

```json
{
  "user_id": "local-dev",
  "total_storage_used": 1234,
  "original_storage_used": 2468,
  "storage_savings": 1234,
  "savings_percentage": 50.0
}
```

`total_storage_used` counts physical bytes for non-reference records only. `original_storage_used` counts every logical record, including references.

### File types

```http
GET /api/files/file_types/
```

Example:

```bash
curl -H "UserId: local-dev" \
  http://localhost:8000/api/files/file_types/
```

Response:

```json
["application/pdf", "image/png", "text/plain"]
```

The list is distinct, sorted, and scoped to the requesting `UserId`.

## Configuration reference

| Setting | Default | Purpose |
| --- | --- | --- |
| `DJANGO_DEBUG` | `True` in Docker Compose | Enables development behavior. Set to `False` for production-like runs. |
| `DJANGO_SECRET_KEY` | `insecure-dev-only-key` in Docker Compose | Django secret key. Replace outside local development. |
| `RATE_LIMIT_CALLS` | `2` | Max API calls per user per sliding window. |
| `RATE_LIMIT_PERIOD` | `1` | Sliding-window size in seconds. |
| `REDIS_URL` | set in Docker Compose | Enables the Redis Lua/ZSET rate limiter and Django's Redis cache backend. Unset uses LocMemCache fallback. |
| `GUNICORN_WORKERS` | `2` in Docker Compose, `1` in `start.sh` | Gunicorn worker process count. |
| `STORAGE_LIMIT_BYTES` | `10 * 1024 * 1024` | Per-user actual storage quota for physical originals. |
| `MAX_UPLOAD_SIZE_BYTES` | `10 * 1024 * 1024` | Per-file upload limit. |
| `ENCRYPTION_KEY` | unset in dev | Base64-url-safe encoded 32-byte AES key. Required when `DEBUG=False`. |
| `ENCRYPTION_CHUNK_SIZE_BYTES` | `1024 * 1024` | Plaintext chunk size for AES-GCM chunked encryption. |
| `DEFAULT_PAGE_SIZE` | `20` | Default list pagination size. |
| `MAX_PAGE_SIZE` | `100` | Maximum list pagination size. |

When `ENCRYPTION_KEY` is unset and `DEBUG=True`, the encryption service derives a local development key from `DJANGO_SECRET_KEY`. Production-like runs must set `ENCRYPTION_KEY`; it must decode to exactly 32 bytes.

Generate a compatible key with Python:

```bash
python -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; import base64; print(base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode())"
```

## Implementation notes

- Deduplication is per-user. The same bytes uploaded by two different `UserId` values create separate originals to avoid cross-user information leakage.
- The unique database constraint on `(user_id, file_hash)` for originals is the source of truth for same-user duplicate races.
- References use `is_reference=True`, `original_file=<original id>`, and no physical file field.
- Storage quota tracks actual encrypted file ownership after deduplication. Reference uploads cost zero quota.
- Search uses `django-filter` and ORM predicates, so query parameters are parameterized rather than hand-written SQL.
- The rate limiter uses Redis sorted sets plus a Lua script when `REDIS_URL` is set, so pruning, counting, and admitting a request are atomic across Gunicorn workers.
- To run without Redis in local Python, leave `REDIS_URL` unset; the throttle falls back to a Django LocMemCache timestamp list.
- SQLite and Docker volumes are intentionally used for the challenge. The service boundaries support later swaps to PostgreSQL, Redis, and object storage.

## Project status

- Steps 1-15 are implemented: setup, E2E client, data model, `UserId` middleware, upload, encryption, deduplication, filtering, delete cascade, storage stats, file types, throttling, quotas, edge cases, README finalization, and Redis-backed multi-worker rate limiting.
- Implemented extras include chunked AES-GCM encryption/streaming downloads and optional sanity E2E tests for the provided fixture files.
- NICE follow-ups remain out of scope for this submission: OpenAPI docs and service container extraction.
