# Ask the Vault Step 6: Chroma/Embedding Tests

## Summary
Add the tests-only slice for v02 Build Plan Step 6. This step defines the expected Chroma/OpenAI embedding and cleanup contract, and should fail until Step 7 implements the ephemeral vector index.

## Key Changes
- Add `backend/files/tests/test_rag_chroma.py` for the future `files.services.rag_index.RagSessionIndex`.
- Test that the future index:
  - Uses `chromadb.EphemeralClient()`.
  - Creates LangChain `Chroma` with no `persist_directory`.
  - Uses collection configuration `{"hnsw": {"space": "cosine"}}`.
  - Configures `OpenAIEmbeddings` from `RAG_EMBEDDING_MODEL` and `RAG_EMBEDDING_DIMENSIONS`.
  - Converts Step 5 chunk dictionaries into documents preserving `page_content` and metadata.
  - Supplies stable Chroma IDs as `{file_id}:{chunk_index}`.
  - Calls `delete_collection()` during cleanup and clears vector/client references.
- Add a WebSocket consumer cleanup contract test:
  - After `select` successfully indexes chunks, `AskVaultConsumer.disconnect()` must call the session index cleanup method.
  - This locks the design requirement that the WebSocket connection is the cleanup boundary.
- Add/extend settings tests for:
  - `OPENAI_API_KEY`
  - `RAG_EMBEDDING_MODEL = "text-embedding-3-small"`
  - `RAG_EMBEDDING_DIMENSIONS = 1536`

## Test Plan
- Run the new Step 6 target:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_chroma.py -q`
  - Expected before Step 7: fails because the vector index service and consumer wiring are missing.
- Run protocol regression:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py -q`
- Run ingest regression when PostgreSQL is available:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ingest.py -q`

## Assumptions
- “Step 6” means v02 TDD Build Plan Step 6: Chroma/embedding tests only.
- Real Chroma/OpenAI implementation belongs to Step 7.
- No retrieval, score thresholding, prompting, or LLM streaming is included in this step.
