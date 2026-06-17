# Step 3: Ask the Vault Runtime Wiring

## Summary
Wire the existing Channels/ASGI skeleton into Docker Compose as a separate `rag_ws` runtime on port `8001`, while leaving the REST API on Gunicorn/WSGI port `8000`. This step does not add real RAG ingest, Chroma, embeddings, retrieval, LLM behavior, or Dockerfile changes.

## Key Changes
- Update `docker-compose.yml` to add a `rag_ws` service using the same `./backend` image and codebase.
- Run `rag_ws` with `uvicorn core.asgi:application --host 0.0.0.0 --port 8001`.
- Publish `8001:8001`, mount the shared `backend_storage:/app/media` volume, and pass the same `DATABASE_URL`, `REDIS_URL`, `DJANGO_DEBUG`, and `DJANGO_SECRET_KEY` values used by `backend`.
- Add `OPENAI_API_KEY=${OPENAI_API_KEY:-}` to `rag_ws` only as future configuration plumbing; Step 3 will not call OpenAI.
- Keep migrations owned by the existing `backend` startup path to avoid competing migration runners.
- Do **not** update `backend/Dockerfile`; `EXPOSE 8001` is only metadata and is unnecessary because Compose publishes `8001`.
- Update docs/smoke instructions to document:
  - REST API: `http://localhost:8000`
  - Ask the Vault WS: `ws://localhost:8001/ws/ask-vault/?user_id=<user-id>`
  - A minimal WebSocket handshake smoke check.

## Public Interface
- New local WebSocket endpoint through Docker Compose:
  `ws://localhost:8001/ws/ask-vault/?user_id=<user-id>`
- Existing REST endpoints and `UserId` header behavior remain unchanged on `http://localhost:8000`.

## Test Plan
- Run existing protocol regression:
  `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py -q`
- Validate Compose syntax:
  `docker compose config`
- Runtime smoke:
  `docker compose up --build`
- Confirm REST still responds:
  `curl -H "UserId: local-dev" http://localhost:8000/api/files/`
- Confirm WS service accepts upgrade on `8001` with valid `user_id`, and rejects missing/blank `user_id` according to existing protocol behavior.

## Assumptions
- Step 3 is limited to runtime wiring from the v02 build plan.
- No RAG libraries beyond existing `channels` and `uvicorn` are required yet.
- Automated Docker E2E RAG tests are deferred to Step 12.
