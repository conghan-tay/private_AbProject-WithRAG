# Step 10: Storage Stats and File Types

## Summary
Add `FileQueryService.get_storage_stats()` and `FileQueryService.get_file_types()`, then expose them through `/api/files/storage_stats/` and `/api/files/file_types/`. Current baseline is clean: `40 passed` for `backend/files/tests`.

## Key Changes
- Extend `FileQueryService`:
  - `get_storage_stats(user_id)` aggregates current user rows only:
    - `total_storage_used`: `SUM(size)` for `is_reference=False`
    - `original_storage_used`: `SUM(size)` for all rows
    - `storage_savings`: `original_storage_used - total_storage_used`
    - `savings_percentage`: formula result as a float, `0.0` when no uploads exist
    - include `user_id` in the returned dict
  - `get_file_types(user_id)` returns `list[str]` from `SELECT DISTINCT file_type WHERE user_id=X ORDER BY file_type ASC`.
- Add `FileViewSet` collection actions:
  - `@action(detail=False, methods=['get']) storage_stats(...)`
  - `@action(detail=False, methods=['get']) file_types(...)`
  - Both use `request.user_id` from existing middleware and return `200`.
- No serializer, model, URL, or migration changes are needed; DRF router will expose the action routes automatically.

## Test Plan
- Add backend tests for:
  - empty user stats return zero totals and `savings_percentage: 0.0`.
  - stats count only the requesting user.
  - dedup references increase `original_storage_used` but not `total_storage_used`.
  - stats update after delete because they are live DB aggregates.
  - `file_types` returns sorted distinct MIME strings for the requesting user only.
  - both endpoints are reachable through the API and match the PRD response shapes.
- Run `.venv/bin/python -m pytest backend/files/tests -q`.
- Optionally run the targeted E2E cases once the server is running:
  - `tests/e2e/test_e2e.py::test_duplicate_upload_creates_reference_and_storage_savings`
  - `tests/e2e/test_e2e.py::test_file_types_endpoint_returns_user_scoped_mime_types`

## Assumptions
- `savings_percentage` should follow the PRD formula directly and return a normal JSON float, with no rounding policy added.
- `file_types` includes every non-null MIME type for the user; current upload code always stores a MIME type, so no blank/null filtering is needed.
- Step 10 does not implement quota enforcement or rate limiting; those remain later build-plan steps.
