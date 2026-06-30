# Step 9 Retrieval Implementation Plan

## Summary
Implement Step 9 of `AskTheVault_RAG_Session_Design_v02.md`: make the Step 8 retrieval tests pass by adding retrieval settings, implementing session-scoped retrieval on `RagSessionIndex`, and wiring `AskVaultConsumer.run_answer` to emit `no_answer` or terminal `done.sources`. LLM token streaming remains deferred to Steps 10/11.

## Key Changes
- Add missing retrieval settings in `backend/core/settings.py`:
  - `RAG_RETRIEVAL_K=4`
  - `RAG_RETRIEVAL_FETCH_K=12`
  - `RAG_MAX_CONTEXT_CHUNKS=4`
  - `RAG_MAX_DISTANCE=0.35`
- Add `RagSessionIndex.retrieve(question)` in `backend/files/services/rag_index.py`:
  - Call `vector_store.similarity_search_with_score(question, k=1)` for thresholding.
  - Treat Chroma cosine scores as distance, where lower is better.
  - Return unanswerable when no score exists or top distance exceeds `RAG_MAX_DISTANCE`.
  - Use MMR retriever with configured `k` and `fetch_k` only after threshold passes.
  - Cap context docs to `RAG_MAX_CONTEXT_CHUNKS`.
  - Return sorted unique file-level `sources` from retrieved doc metadata.
- Update `AskVaultConsumer.run_answer(question)` in `backend/files/consumers.py`:
  - Run blocking retrieval through `sync_to_async(..., thread_sensitive=True)`.
  - If retrieval is unanswerable, yield `{ "type": "no_answer", "reason": "not_in_documents" }`.
  - If answerable, delegate to `generate_answer_messages(question, documents, sources)`.
  - Add default `generate_answer_messages(...)` that yields terminal `done` with deterministic sources only, leaving token streaming for Step 11.

## Test Plan
- Run focused Step 9 tests:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_retrieval.py -q`
- Run regression tests for existing protocol/index behavior:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py -q`
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_chroma.py -q`
- If local Postgres is available, also run:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ingest.py -q`

## Assumptions
- Step 9 intentionally stops before LLM streaming, prompt assembly, `llm_failed`, and token messages.
- `retrieval_failed` is documented for the broader feature but is not covered by the current Step 8 tests; avoid adding unrelated error behavior unless a failing test requires it.
- The existing fake-based tests are the source of truth for this step’s contract.
