# Step 8: Retrieval Tests for Ask the Vault

## Summary
Implement v02 TDD Build Plan Step 8 as a tests-only slice. This defines the retrieval contract for Step 9 and should intentionally fail until retrieval is implemented. No production retrieval, prompt, or LLM streaming code belongs in this step.

Baseline found:
- `test_rag_ws_protocol.py`: 27 passed.
- `test_rag_chroma.py`: 9 passed.
- `test_rag_ingest.py`: blocked by local Postgres not running on `localhost:5432`.

## Key Changes
- Add `backend/files/tests/test_rag_retrieval.py`.
- Define the expected `RagSessionIndex` retrieval contract:
  - Uses `similarity_search_with_score` for thresholding.
  - Treats Chroma cosine scores as distances, where lower is better.
  - Returns `no_answer` when the best distance is greater than `settings.RAG_MAX_DISTANCE`.
  - Uses MMR retrieval with `k=settings.RAG_RETRIEVAL_K` and `fetch_k=settings.RAG_RETRIEVAL_FETCH_K`.
  - Caps returned context docs to `settings.RAG_MAX_CONTEXT_CHUNKS`.
  - Produces deterministic sorted file-level sources from retrieved doc metadata.
- Add tests for retrieval-related settings defaults:
  - `RAG_RETRIEVAL_K = 4`
  - `RAG_RETRIEVAL_FETCH_K = 12`
  - `RAG_MAX_CONTEXT_CHUNKS = 4`
  - `RAG_MAX_DISTANCE = 0.35`
- Add WebSocket-level tests using fakes:
  - Off-topic retrieval emits `{ "type": "no_answer", "reason": "not_in_documents" }`.
  - Relevant retrieval terminates with `done.sources` sorted deterministically.
  - The `no_answer` path does not call any answer-generation hook.

## Test Cases
- Score direction: distance `0.12` is answerable; distance above `RAG_MAX_DISTANCE` is not.
- MMR config: fake vector store records `search_type="mmr"` and exact `search_kwargs`.
- Source ordering: duplicate and unsorted metadata source IDs become sorted unique sources.
- Context cap: more retrieved docs than `RAG_MAX_CONTEXT_CHUNKS` are truncated before answer context is returned.
- Wire behavior: `ask` after ready returns either `no_answer` or terminal `done` based on fake retrieval result.

## Verification
- Run:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_retrieval.py -q`
  - Expected after Step 8 only: failing tests because retrieval is not implemented.
- Regression checks:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py -q`
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_chroma.py -q`
- Run ingest tests only when Postgres is available:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ingest.py -q`

## Assumptions
- Step 8 means the TDD Build Plan row “Retrieval tests,” not retrieval implementation.
- Step 9 will make these tests pass by adding the retrieval implementation.
- LLM token streaming, `llm_failed`, prompt assembly, and external LLM client behavior remain deferred to Steps 10 and 11.
