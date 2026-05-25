**Restore `file` In API Responses**

**Summary**
- Add the PDF-required `file` field to list, upload, and detail responses while preserving the existing multipart upload field named `file`.
- For original files, return the stored media path, e.g. `/media/uploads/<uuid>.pdf`.
- For reference records, return the original file’s stored media path, matching the PDF example where duplicates point at existing bytes.

**Implementation Changes**
- Update `FileSerializer` in `backend/files/serializers.py`:
  - Keep `file = serializers.FileField(write_only=True, allow_empty_file=True)` for upload input.
  - Override `to_representation()` to inject a response-only `file` key.
  - Resolve the effective storage record as `instance.original_file` when `instance.is_reference` is true, otherwise `instance`.
  - Return `storage_record.file.url` when available; return `None` only for malformed references with no original/storage file.
- Keep the existing `download` endpoint as the authenticated way to retrieve plaintext bytes. The `file` response value is metadata/path compatibility for the original contract, not a replacement for `/api/files/{id}/download/`.
- Update README API examples so list/upload/detail responses include `file`.

**Test Plan**
- Add or update serializer/API tests to assert:
  - Upload response includes `file`.
  - List response includes `file`.
  - Detail response includes `file`.
  - Duplicate upload response has `is_reference: true` and `file` equal to the original file’s `file` value.
- Update e2e metadata contract helper in `tests/e2e/test_e2e.py` to require `file`.
- Run:
  - `.venv/bin/python -m pytest backend/files/tests -q`
  - `docker compose up --build -d`
  - `.venv/bin/python -m pytest tests/e2e -q`

**Assumptions**
- The original PDF’s `file` field is treated as a response metadata path, not as an unauthenticated plaintext download mechanism.
- Returning the effective original file path for references is preferred over returning `null`, because the PDF example shows duplicate references pointing to the same stored file path.
