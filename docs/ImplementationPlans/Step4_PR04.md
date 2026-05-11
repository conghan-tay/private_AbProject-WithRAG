# Step 4: UserIdMiddleware

## Summary
Implement API-scoped `UserId` header enforcement for `/api/` routes. Missing header returns `401`, empty or whitespace-only header returns `400`, and valid values are attached as `request.user_id` for downstream views.

## Key Changes
- Add `backend/files/middleware.py` with `UserIdMiddleware`.
- Register it in `backend/core/settings.py` before DRF views run, while limiting enforcement to paths starting with `/api/`.
- Response contract:
  - Missing `UserId`: `{"detail": "UserId header required"}`, HTTP `401`.
  - Empty/whitespace `UserId`: `{"detail": "UserId must not be empty"}`, HTTP `400`.
  - Valid `UserId`: attach stripped value to `request.user_id`.
- Update `FileViewSet.get_queryset()` to scope list/retrieve base query by `request.user_id`.
- Leave upload, deduplication, filtering, throttling, quota, and final serializer contract for later build steps.
- Update README build progress/auth notes for Step 4.

## Tests
- Add `backend/files/tests/test_auth.py`.
- Cover:
  - `/api/files/` without `UserId` returns `401`.
  - `/api/files/` with `UserId: ""` returns `400`.
  - `/api/files/` with whitespace-only `UserId` returns `400`.
  - `/api/files/` with valid `UserId` returns `200` and reaches the view.
  - Middleware attaches `request.user_id` to the request object.
  - Non-API paths bypass this middleware because the chosen scope is API-only.
- Use patched empty querysets where needed so Step 4 tests do not depend on upload or database fixture work.

## Verification
- Run `.venv/bin/python -m pytest backend/files/tests -q`.
- Existing full E2E suite is still expected to fail until later PRD build steps, but the missing-header E2E behavior should now match the final contract.

## Assumptions
- Middleware enforcement is API-only, per the selected option, to avoid breaking admin/static/development paths.
- Whitespace-only `UserId` is treated as empty.
- Step 4 should make valid authenticated list requests possible, but should not implement upload metadata, hashing, deduplication, or rate limiting yet.
