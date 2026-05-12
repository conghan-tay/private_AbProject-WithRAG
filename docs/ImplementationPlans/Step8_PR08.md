# Step 8 Plan: File Search, Filtering, and Pagination

## Summary
Implement Step 8 without changing upload/dedup behavior: add `FileFilter`, add `FileQueryService.build_queryset(user_id, filters)`, and wire `GET /api/files/` to return a paginated, user-scoped, filtered envelope. Current backend baseline is clean: `.venv/bin/python -m pytest backend/files/tests -q` passes `24` tests.

## Key Changes
- Add `backend/files/filters.py` with a `django_filters.FilterSet` for:
  - `search` → `original_filename__icontains`
  - `file_type` → exact MIME match
  - `min_size` / `max_size` → `size__gte` / `size__lte`
  - `start_date` / `end_date` → `uploaded_at__gte` / `uploaded_at__lte` using ISO datetime parsing
- Add `backend/files/services/query.py` with `FileQueryService.build_queryset(user_id, filters)`:
  - starts from `File.objects.filter(user_id=user_id)`
  - applies `FileFilter` with AND semantics
  - preserves model default ordering of newest first
  - raises DRF `ValidationError` for invalid filter values rather than silently ignoring them
- Update `FileViewSet`:
  - keep `get_queryset()` as user-only scoping for retrieve/download safety
  - override `list()` to call `FileQueryService.build_queryset(request.user_id, request.query_params)`
  - use DRF page-number pagination and serialize either the page or queryset
- Add pagination settings:
  - `DEFAULT_PAGE_SIZE = 20`
  - `MAX_PAGE_SIZE = 100`
  - page query params remain `page` and `page_size`
- No migration should be needed because the Step 3 model already contains the required indexes.

## Public API
- `GET /api/files/` returns `{count, next, previous, results}`.
- Supported query params: `search`, `file_type`, `min_size`, `max_size`, `start_date`, `end_date`, `page`, `page_size`.
- Filters are user-scoped and AND-composed.
- Invalid numeric/date filters return `400`.

## Test Plan
- Add backend tests for:
  - case-insensitive filename search
  - exact MIME filtering
  - size range filtering
  - ISO date range filtering
  - simultaneous filters returning the intersection
  - no matches returning `count: 0` and empty `results`
  - pagination with `page=2&page_size=5`
  - cross-user isolation in list/search results
  - invalid filter values returning `400`
- Run `.venv/bin/python -m pytest backend/files/tests -q`.
- Optionally run the relevant E2E list test once the API server is running.

## Assumptions
- Step 8 does not implement `storage_stats`, `file_types`, quotas, throttling, or delete cascade.
- Existing Step 3 indexes are considered “applied”; this step only uses them through ORM query shape.
- Detail, download, and future delete lookups must remain scoped only by `UserId`, not affected by list filter query params.
