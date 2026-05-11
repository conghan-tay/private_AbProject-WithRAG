# Step 5: File Upload Without Deduplication

## Summary
Implement `POST /api/files/` so authenticated users can upload multipart files, have metadata persisted, and receive the PRD metadata contract. This step will not create references or enforce deduplication yet, but it will compute and store `file_hash` because the Step 3 model requires it and later dedup depends on it.

## Key Changes
- Configure Django to use `TemporaryFileUploadHandler` for uploads and add `MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024` in settings.
- Update `FileSerializer` so `file` is upload-only and responses expose metadata: `id`, `user_id`, `original_filename`, `file_type`, `size`, `file_hash`, `is_reference`, `original_file`, `reference_count`, `uploaded_at`.
- Update `FileViewSet.create()` to:
  - require multipart `file`,
  - use `request.user_id`,
  - detect MIME type with `python-magic` from file contents, not request content type,
  - compute SHA-256 while resetting file pointers correctly,
  - save a normal original record with `is_reference=False`, `original_file=None`, `reference_count=1`.
- Keep duplicate uploads as separate original records for now; no duplicate lookup, reference creation, unique constraint, encryption, quota, or throttling in this step.

## Test Plan
- Add focused upload tests under `backend/files/tests/`:
  - real PDF upload returns `201`,
  - response contains PRD metadata fields,
  - `user_id` comes from the `UserId` header,
  - `file_type` is detected as `application/pdf` even if multipart content type is misleading,
  - `file_hash` is a 64-character SHA-256 hex digest,
  - file is written under isolated temp media during the test.
- Run existing backend tests plus the new upload test path.
- Optionally run the current E2E upload test against Docker; only the upload metadata portion is expected to improve, while later features should still fail.

## Assumptions
- “No dedup yet” means no duplicate lookup/reference behavior, not “skip hashing.”
- Exact missing-file error shape is not locked by the docs; use DRF serializer validation with HTTP `400`.
- Encryption remains Step 6, so disk bytes are still plaintext after Step 5.
