# Step 15: Redis-Backed Multi-Worker Rate Limiting

## Summary
Implement Redis as the default Docker cache backend and run the backend with two Gunicorn workers so the existing sliding-window throttle is enforced across processes. Preserve local fallback to LocMemCache when `REDIS_URL` is unset.

## Key Changes
- Add Redis to `docker-compose.yml` as an internal service, set backend `REDIS_URL=redis://redis:6379/0`, and set `GUNICORN_WORKERS=2`.
- Un-comment/add the pinned `redis` Python dependency in `backend/requirements.txt`.
- Update `backend/core/settings.py`:
  - use Django `RedisCache` when `REDIS_URL` is present
  - use named `LocMemCache` fallback otherwise
  - document the swap directly near `CACHES`
- Update `backend/start.sh` to read `GUNICORN_WORKERS`, defaulting to `1` outside Compose.
- Harden `SlidingWindowThrottle` with a short per-user cache lock using Django cache operations so concurrent requests do not lose timestamp updates under Redis.
- Update README and `docs/Mermaid.md` to show Redis as the multi-worker cache path and document `REDIS_URL` / `GUNICORN_WORKERS`.

## Public Interfaces
- No HTTP API changes.
- New runtime configuration:
  - `REDIS_URL`: enables Redis-backed Django cache.
  - `GUNICORN_WORKERS`: controls Gunicorn worker count.
- Docker default changes from single backend container to backend plus Redis, with backend running two workers.

## Test Plan
- Add an E2E test that sends concurrent rapid requests for one unique `UserId` and asserts only `RATE_LIMIT_CALLS` requests succeed while the rest return `429`.
- Run backend tests: `.venv/bin/python -m pytest backend/files/tests -q`.
- Run Docker-backed E2E tests after rebuild: `docker compose up --build`, then `.venv/bin/python -m pytest tests/e2e -q`.
- Smoke-check config path with `curl -H "UserId: local-dev" http://localhost:8000/api/files/`.

## Assumptions
- Redis is enabled by default for Docker Compose, per your choice.
- Multi-worker verification is automated in the E2E suite.
- Redis remains internal to Compose; no host port is exposed unless explicitly needed later.
