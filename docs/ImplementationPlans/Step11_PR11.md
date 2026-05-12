# Step 11: Sliding Window Throttle

## Summary
Implement cache-backed per-user sliding-window throttling for all `FileViewSet` API actions. Current baseline is clean: `45 passed` for `backend/files/tests`.

## Key Changes
- Add `backend/files/throttling.py` with `SlidingWindowThrottle(BaseThrottle)`:
  - cache key: `ratelimit:{request.user_id}`
  - read timestamp list from Django cache, prune entries older than `settings.RATE_LIMIT_PERIOD`
  - allow when remaining count is below `settings.RATE_LIMIT_CALLS`
  - append current timestamp and save with TTL `RATE_LIMIT_PERIOD * 2`
  - raise DRF `Throttled(detail="Call Limit Reached")` on breach so the JSON body is exactly `{"detail": "Call Limit Reached"}`
- Add settings in `backend/core/settings.py`:
  - `RATE_LIMIT_CALLS = int(os.environ.get("RATE_LIMIT_CALLS", 2))`
  - `RATE_LIMIT_PERIOD = float(os.environ.get("RATE_LIMIT_PERIOD", 1))`
- Wire throttling onto `FileViewSet` via `throttle_classes = [SlidingWindowThrottle]`.
- No model, serializer, URL, dependency, or migration changes are needed.

## Test Plan
- Add backend tests for:
  - first and second requests in the window return `200`
  - third request in the same window returns `429` with `detail == "Call Limit Reached"`
  - request after the period expires is allowed
  - separate `UserId` values have independent rate windows
  - custom `RATE_LIMIT_CALLS` and `RATE_LIMIT_PERIOD` settings are honored
  - missing `UserId` still returns middleware `401`, not throttle `429`
- Use isolated LocMemCache settings or `cache.clear()` in tests to avoid cross-test leakage.
- Run `.venv/bin/python -m pytest backend/files/tests -q`.
- Optionally run targeted E2E: `tests/e2e/test_e2e.py::test_rate_limit_returns_429_on_third_rapid_request`.

## Assumptions
- Throttling applies to every `FileViewSet` endpoint, including list, retrieve, upload, delete, download, `storage_stats`, and `file_types`.
- Django’s default local-memory cache is acceptable for Step 11; Redis remains Step 15.
- The exact response body matters more than DRF’s default throttling wait message, so the throttle will raise `Throttled(detail="Call Limit Reached")` directly.
