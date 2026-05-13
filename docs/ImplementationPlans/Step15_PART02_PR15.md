# Refactor Rate Limiter to Redis Lua Sliding Window

## Summary
Replace the Redis-backed per-user cache lock with an idiomatic Redis sorted-set sliding-window limiter. When `REDIS_URL` is set, `SlidingWindowThrottle` will use redis-py directly and execute one Lua script atomically. When `REDIS_URL` is unset, it will keep the Django cache list implementation for local fallback and existing unit tests.

## Key Changes
- Update `SlidingWindowThrottle`:
  - remove `ratelimit-lock:*` keys and lock timing constants
  - branch on `settings.REDIS_URL`
  - Redis path uses `redis.Redis.from_url(settings.REDIS_URL)` plus a Lua script
  - fallback path keeps `cache.get -> prune -> cache.set`
- Redis Lua script behavior:
  - key: `ratelimit:{user_id}`
  - score: current time in milliseconds
  - member: unique request id such as `{now_ms}:{uuid}`
  - `ZREMRANGEBYSCORE` removes entries older than the sliding window
  - `ZCARD` counts active requests
  - if count is at or above `RATE_LIMIT_CALLS`, return deny without adding the request
  - otherwise `ZADD`, `EXPIRE`, return allow
- Keep `CACHES` in `settings.py` as-is for Django cache configuration, but make throttle Redis usage explicit in comments/docs.
- Update README/Mermaid language to say Redis rate limiting uses atomic Lua + sorted sets, while LocMem fallback remains for local no-Redis runs.

## Public Interfaces
- No HTTP API changes.
- `REDIS_URL` remains the switch for Redis-backed multi-worker rate limiting.
- `RATE_LIMIT_CALLS` and `RATE_LIMIT_PERIOD` semantics stay unchanged.
- Redis keys change from list values plus lock keys to sorted sets only; stale lock keys, if any, are ignored.

## Test Plan
- Preserve existing backend throttle tests against the LocMem fallback.
- Add focused unit tests for the Redis branch by mocking the Redis client/script result:
  - script return `1` allows request
  - script return `0` raises `Throttled("Call Limit Reached")`
  - script receives expected key, limit, window, TTL, and unique member arguments
- Keep the existing Docker E2E concurrent test that sends six rapid requests and expects exactly two `200` and four `429`.
- Run:
  - `.venv/bin/python -m pytest backend/files/tests -q`
  - `docker compose up -d --build`
  - `.venv/bin/python -m pytest tests/e2e -q`
  - smoke check `curl -H "UserId: local-dev" http://localhost:8000/api/files/`

## Assumptions
- Redis is the production/default Docker path, so using redis-py directly in the throttle is acceptable.
- LocMem fallback is for local development and tests, not for multi-worker correctness.
- Denied requests should not be recorded in the sliding window, matching the current throttle behavior.
