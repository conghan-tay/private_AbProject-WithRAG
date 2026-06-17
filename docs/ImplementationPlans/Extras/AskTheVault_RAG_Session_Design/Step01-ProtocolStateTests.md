# AskTheVault Step 1: Full Protocol/State Test Skeleton

## Summary
Implement Step 1 as a tests-first slice for the WebSocket state machine at `/ws/ask-vault/?user_id=...`. The tests should intentionally fail until Step 2 adds the minimal Channels consumer, routing, and background-task behavior.

## Key Changes
- Add `channels==4.3.2` to `backend/requirements.txt` so tests can use `channels.testing.WebsocketCommunicator`.
- Add `backend/files/tests/test_rag_ws_protocol.py`.
- Use `WebsocketCommunicator` against `core.asgi.application`.
- Use `asgiref.sync.async_to_sync` so tests remain synchronous and match the current backend test style.
- Assume the future consumer exposes patchable async hooks for ingest and answer streaming, for example `run_ingest(...)` and `run_answer(...)`, so tests can hold the consumer in transient states deterministically.

## Required Test Coverage
- Connection/auth:
  - Missing `user_id` closes with `4401`.
  - Blank or whitespace-only `user_id` closes with `4400`.
  - Valid `user_id` accepts and sends `{"type": "status", "state": "connected_no_documents"}`.
- Request validation:
  - Malformed JSON returns `bad_request`.
  - Unknown `action` returns `bad_request`.
  - Invalid or empty `select.file_ids` returns `bad_request`.
  - Invalid or empty `ask.question` returns `bad_request`.
- State machine:
  - `ask` before `select` returns `no_documents`.
  - `select` while `ingesting` returns `already_selected`.
  - `select` while `ready` returns `already_selected`.
  - `select` while `answering` returns `already_selected`.
  - `ask` while `ingesting` returns `not_ready`.
  - `ask` while `answering` returns `busy`.
  - Successful answer emits terminal `done` and returns the session to `ready`, proven by a second valid `ask` starting normally.

## Step 2 Compatibility Requirement
- The Step 2 implementation should use background tasks for ingest and answer streaming:
  - On `select`, set state to `ingesting`, start ingest in a background task, and keep receiving messages.
  - On successful ingest, transition to `ready`.
  - On `ask`, set state to `answering`, start answer streaming in a background task, and keep receiving messages.
  - On terminal `done` or recoverable answer failure, transition back to `ready`.
- This is required so transient-state tests are real behavior tests, not unreachable branches.

## Test Plan
- Install dependencies after editing requirements:
  - `.venv/bin/python -m pip install -r backend/requirements.txt`
- Run the new targeted tests:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py -q`
- Expected Step 1 result: tests collect and fail because the consumer/routing/hooks are not implemented yet.
- Run a small REST regression:
  - `.venv/bin/python -m pytest backend/files/tests/test_auth.py -q`

## Assumptions
- “Step 1” means Section 9 TDD Build Plan Step 1 in `AskTheVault_RAG_Session_Design_v02.md`.
- Step 1 is intentionally red; making these tests pass is Step 2.
- Existing REST API behavior, upload/download encryption, deduplication, PostgreSQL, and Redis behavior remain unchanged.
