# Step 12: AskTheVault Docker E2E Smoke

## Summary
Implement Step 12 from `AskTheVault_RAG_Session_Design_v02.md`: a Docker-backed E2E smoke test that uploads a TXT file over REST, selects it over the Ask the Vault WebSocket, asks a question, receives streamed tokens, and verifies terminal source attribution.

## Key Changes
- Add `ASKVAULT_RAG_E2E_FAKE`, a `DEBUG=True` only setting for deterministic Docker E2E runs.
- Add local fake embeddings and answer streaming in `files.services.rag_fake`.
- Wire fake embeddings through `RagSessionIndex` while preserving Chroma `EphemeralClient` indexing and retrieval.
- Wire fake answer streaming through `RagAnswerService` while preserving the normal ChatOpenAI path when fake mode is disabled.
- Add `tests/e2e/test_rag_ws.py`, skipped unless `RUN_ASKVAULT_RAG_E2E=1`.
- Extend `scripts/smoke_docker_runtime.sh` to start Compose with fake RAG enabled and run the focused RAG E2E smoke.

## Public Contract
- No REST API or WebSocket protocol changes.
- New local/test-only environment variables:
  - `ASKVAULT_RAG_E2E_FAKE=True` enables deterministic local RAG behavior in `rag_ws`.
  - `RUN_ASKVAULT_RAG_E2E=1` opts into the focused E2E pytest.
  - `FILE_VAULT_RAG_WS_URL` optionally overrides the WebSocket base URL.

## Tests
- `.venv/bin/python -m pytest backend/files/tests/test_rag_answer.py backend/files/tests/test_rag_chroma.py backend/files/tests/test_rag_retrieval.py backend/files/tests/test_rag_streaming.py -q`
- `RUN_ASKVAULT_RAG_E2E=1 .venv/bin/python -m pytest tests/e2e/test_rag_ws.py -q` against a Compose stack started with `ASKVAULT_RAG_E2E_FAKE=True`.
- `./scripts/smoke_docker_runtime.sh`

## Assumptions
- Step 12 should be deterministic and cost-free by default, so it does not require live OpenAI calls.
- The fake mode is local smoke-test plumbing only and is refused when `DJANGO_DEBUG=False`.
