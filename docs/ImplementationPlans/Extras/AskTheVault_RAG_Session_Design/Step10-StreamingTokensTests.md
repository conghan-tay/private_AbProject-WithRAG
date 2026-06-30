# Step 10: AskTheVault Streaming Tests

## Summary
Add the Step 10 TDD slice only: focused failing tests for answer streaming over the existing WebSocket/RAG hooks. Do not implement the LLM stream bridge yet; that remains Step 11.

Current baseline verified: `test_rag_ws_protocol.py` and `test_rag_retrieval.py` pass, 37 tests total.

## Key Changes
- Add streaming-focused tests in `backend/files/tests/test_rag_streaming.py`.
- Reuse the existing `AskVaultConsumer` patching pattern with fake session indexes and fake retrieved documents.
- Add/expect `protocol.ERROR_LLM_FAILED == "llm_failed"` so the documented wire contract is pinned before implementation.
- Test successful relevant-answer streaming:
  - fake retrieval returns `answerable=True`, context docs, and sorted sources.
  - future answer generator must emit one `{type: "token", data: ...}` per token.
  - stream must terminate with `{type: "done", sources: [...]}`.
  - a second ask after `done` must work, proving state returned to `ready`.
- Test LLM failure after partial tokens:
  - at least one token is emitted first.
  - failure emits `{type: "error", code: "llm_failed"}`.
  - no terminal `done` follows.
  - a later ask is accepted, proving state returned to `ready`.

## Public Contract
- WebSocket message contract remains:
  - token: `{ "type": "token", "data": "<text>" }`
  - success terminal: `{ "type": "done", "sources": ["<file-id>", ...] }`
  - LLM failure: `{ "type": "error", "code": "llm_failed" }`
- Source IDs are still deterministic and derived from retrieval metadata, not model output.
- No OpenAI or external LLM calls are made in tests.

## Test Plan
- Run the new Step 10 tests and confirm they fail for the current placeholder implementation:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_streaming.py -q`
- Run existing RAG regressions to ensure Step 10 tests do not disturb prior contracts:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py backend/files/tests/test_rag_retrieval.py -q`

## Assumptions
- Step 10 is tests-only, matching the v02 TDD Build Plan.
- Step 11 will add the production implementation for prompt assembly and the sync/async LLM streaming bridge.
- It is acceptable for the new Step 10 tests to be red immediately after this slice.
- Step 10 pins a sync iterator token-stream hook, but does not test that
  iteration is offloaded from the event loop; Step 11 implementation tests must
  verify that sync LLM iteration runs off the shared Channels event loop.
