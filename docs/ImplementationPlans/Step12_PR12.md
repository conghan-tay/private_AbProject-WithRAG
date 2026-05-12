# Step 12: FileQueryService Quota Enforcement

## Summary
Implement per-user storage quota enforcement for new physical file writes only. The current baseline is clean: `50 passed` in `backend/files/tests`. Step 12 should add `check_quota()`, wire it into the upload flow before encryption/storage, and return exact `429 {"detail": "Storage Quota Exceeded"}` when the configured quota would be breached.

## Key Changes
- Add `STORAGE_LIMIT_BYTES = int(os.environ.get("STORAGE_LIMIT_BYTES", 10 * 1024 * 1024))` in `backend/core/settings.py`.
- Add `QuotaExceededException` in `backend/files/services/query.py` as a DRF `APIException` with:
  - `status_code = 429`
  - `default_detail = "Storage Quota Exceeded"`
  - stable default code such as `"storage_quota_exceeded"`
- Add `FileQueryService.check_quota(user_id, new_size)`:
  - aggregate `SUM(size)` for `File.objects.filter(user_id=user_id, is_reference=False)`
  - treat `None` as `0`
  - allow when `current_used + new_size <= settings.STORAGE_LIMIT_BYTES`
  - raise `QuotaExceededException` when over limit
  - ignore references because quota tracks actual bytes on disk after deduplication
- Wire upload in `FileViewSet.create()`:
  - keep current order: validate file, detect MIME, compute hash, find duplicate
  - if duplicate exists, create reference immediately and bypass quota
  - if no duplicate, call `FileQueryService.check_quota(request.user_id, file_obj.size)` before `EncryptionService.encrypt_file()` and before `record.file.save(...)`
  - keep the existing `IntegrityError` race fallback behavior; if the race resolves to a duplicate, return the reference without rechecking quota

## Public Contract
- `POST /api/files/` returns `429` with `{"detail": "Storage Quota Exceeded"}` when a new original would exceed quota.
- Duplicate uploads for the same user still return `201` as references even when the user is at quota.
- Existing response shapes for upload, list, stats, file types, delete, and download stay unchanged.

## Test Plan
- Add backend quota tests, preferably in a new `backend/files/tests/test_quota.py` or focused additions to `test_query.py`/`test_upload.py`:
  - `check_quota()` allows uploads below quota
  - `check_quota()` allows exact quota boundary
  - `check_quota()` raises `QuotaExceededException` over quota
  - references are excluded from used-storage aggregation
  - quota is scoped per `user_id`
  - API upload over quota returns `429 {"detail": "Storage Quota Exceeded"}`
  - duplicate upload bypasses quota and returns `201` with `is_reference=True`
  - deleting an original frees quota, allowing a later upload
- Run `.venv/bin/python -m pytest backend/files/tests -q`.
- Optionally run targeted E2E once the API is running: `tests/e2e/test_e2e.py::test_quota_exceeded_returns_429_and_duplicate_bypasses_quota`.

## Assumptions
- `MAX_UPLOAD_SIZE_BYTES` remains the per-file ceiling; `STORAGE_LIMIT_BYTES` is the cumulative per-user actual-storage quota.
- Quota enforcement applies only to new originals, not references.
- Equality with the quota is allowed; only values strictly greater than the limit are rejected.
- No migration is needed because quota is computed from existing `File` rows.
