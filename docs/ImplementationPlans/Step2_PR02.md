# Step 2 Plan: E2E Client Skeleton + README Skeleton

## Summary
Implement Step 2 as the project’s failing E2E progress dashboard and starter documentation. The repo currently has only the Django backend and docs, so this step will add the test harness, fixtures/helpers, and root `README.md` without implementing backend features from later build steps.

## Key Changes
- Add a root E2E test structure:
  - `tests/e2e/client.py`: small `requests`-based `FileVaultClient` targeting `FILE_VAULT_BASE_URL` or `http://localhost:8000`, always sending `UserId`.
  - `tests/e2e/test_e2e.py`: pytest tests for the Section 8.6 lifecycle: upload, list, download, duplicate upload, storage stats, delete, rate limit, and quota.
  - Test helpers will use unique `UserId` values per test and wait between non-rate-limit requests so the suite remains valid once throttling is implemented.
- Add minimal fixture support:
  - Use lightweight fixture files or generated temp files for sample text/PDF/JPEG-style uploads and quota payloads.
  - Keep fixture data small except the quota test, which will generate repeated 1 MiB payloads at runtime.
- Add test tooling:
  - Add `pytest` to `backend/requirements.txt` because the architecture doc names pytest but the current repo only pins `requests`.
  - Optionally add a minimal root `pytest.ini` with `testpaths = tests/e2e`.
- Add root `README.md` skeleton:
  - Project purpose and architecture overview.
  - Docker startup instructions: `docker compose up --build`.
  - E2E dashboard instructions: `python -m pytest tests/e2e -q`.
  - API service map for `/api/files/`, `/api/files/{id}/download/`, `/api/files/storage_stats/`, and `/api/files/file_types/`.
  - Build-plan progress section noting Step 2 creates intentionally failing tests that should turn green as later steps land.

## Test Plan
- With the server stopped, `python -m pytest tests/e2e -q` should fail cleanly with a clear “server unavailable” message.
- With the current backend running, the E2E suite should run and fail on final-contract assertions, not crash from missing imports or malformed requests.
- Confirm the client targets `/api/files/` and uses the `UserId` header consistently.

## Assumptions
- The README should be created at the repo root as `README.md`.
- Pytest is acceptable as a Step 2 addition because the architecture document explicitly defines the E2E layer as pytest plus requests.
- This step will not implement middleware, throttling, deduplication, encryption, download, quota, or filtering behavior; tests will assert the final contract and fail until later build steps implement it.
