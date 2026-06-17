# Step 2 Plan: ASGI/Channels Skeleton for Ask the Vault

## Summary
Implement the minimal Channels WebSocket layer needed to make `backend/files/tests/test_rag_ws_protocol.py` pass. This step only covers routing, protocol validation, session state, and patchable background hooks. It does not implement real file ingest, Chroma, embeddings, retrieval, LLM calls, Docker `rag_ws`, or RAG dependencies beyond what Step 1 already added.

## Key Changes
- Add `files.consumers.AskVaultConsumer` using `channels.generic.websocket.AsyncWebsocketConsumer`.
- Add `files.routing.websocket_urlpatterns` for `/ws/ask-vault/`.
- Update `core.asgi.application` to use `ProtocolTypeRouter`:
  - HTTP remains `get_asgi_application()`.
  - WebSocket routes go through `URLRouter(files.routing.websocket_urlpatterns)`.
- Add Channels app settings if missing:
  - Include `"channels"` in `INSTALLED_APPS`.
  - Set `ASGI_APPLICATION = "core.asgi.application"`.

## Consumer Behavior
- On connect:
  - Parse `user_id` from the query string.
  - Missing `user_id`: close with `4401`.
  - Blank/whitespace `user_id`: close with `4400`.
  - Valid `user_id`: accept and send `{"type": "status", "state": "connected_no_documents"}`.
- Validate inbound JSON:
  - Malformed JSON, unknown action, invalid `select.file_ids`, or invalid `ask.question` sends `{"type": "error", "code": "bad_request"}`.
  - `select.file_ids` must be a non-empty list of valid UUID strings.
  - `ask.question` must be a non-blank string.
- Implement state rules:
  - `select` from `connected_no_documents`: set `ingesting`, send status, start background ingest task.
  - `select` from `ingesting`, `ready`, or `answering`: send `already_selected`.
  - `ask` from `connected_no_documents`: send `no_documents`.
  - `ask` from `ingesting`: send `not_ready`.
  - `ask` from `answering`: send `busy`.
  - `ask` from `ready`: set `answering`, start background answer task.
- Expose patchable hooks:
  - `async def run_ingest(self, file_ids)` returns `{"indexed_files": 0, "skipped_files": []}` by default.
  - `async def run_answer(self, question)` is an async generator yielding `{"type": "done", "sources": []}` by default.
- Background task completion:
  - Ingest success sends `{"type": "ready", "indexed_files": ..., "skipped_files": ...}` and sets state to `ready`.
  - Answer task forwards yielded messages directly and sets state back to `ready` after terminal completion.
  - On disconnect, cancel any active ingest/answer task and set state to `disconnected`.

## Test Plan
- Run the targeted Step 2 suite:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py -q`
  - Expected: all 19 tests pass.
- Run a REST regression check:
  - `.venv/bin/python -m pytest backend/files/tests/test_auth.py -q`
  - Expected: existing HTTP auth behavior remains unchanged.
- Optionally run the full backend suite if time permits:
  - `.venv/bin/python -m pytest backend/files/tests -q`

## Assumptions
- Step 2 is intentionally limited to making Step 1 protocol/state tests green.
- `docker-compose.yml` changes for a separate `rag_ws` service belong to Step 3, not this step.
- Real ingest, Chroma lifecycle, embeddings, retrieval, and LLM streaming remain deferred to Steps 4-11.
