# Step 7: Ephemeral RAG Index Implementation

## Summary
Implement the v02 Step 7 slice only: create a per-WebSocket-session Chroma `EphemeralClient` index, populate it from Step 5 TXT chunks, and clean it up on disconnect. Do not add retrieval, score thresholding, prompting, or LLM streaming yet.

## Key Changes
- Add RAG embedding configuration in `backend/core/settings.py`:
  - `OPENAI_API_KEY`
  - `RAG_EMBEDDING_MODEL = "text-embedding-3-small"`
  - `RAG_EMBEDDING_DIMENSIONS = 1536`
- Add Step 7 dependencies to `backend/requirements.txt` using the v02 pins:
  - `langchain==1.3.9`
  - `langchain-openai==1.3.2`
  - `langchain-chroma==1.1.0`
  - `chromadb==1.5.9`
- Add `files.services.rag_index.RagSessionIndex`:
  - Constructs `chromadb.EphemeralClient()`.
  - Constructs `OpenAIEmbeddings(model=settings.RAG_EMBEDDING_MODEL, dimensions=settings.RAG_EMBEDDING_DIMENSIONS)`.
  - Constructs `Chroma(..., collection_name=f"askvault-{session_id}", collection_configuration={"hnsw": {"space": "cosine"}})`.
  - Never passes `persist_directory`.
  - Converts chunk dicts into `Document(page_content=..., metadata=...)`.
  - Uses stable IDs: `{file_id}:{chunk_index}`.
  - `cleanup()` calls `delete_collection()` and clears Chroma/vector references, swallowing cleanup exceptions.
- Update `AskVaultConsumer`:
  - Initialize `self.session_index = None` during `connect`, including rejected-connect paths.
  - In real `run_ingest`, call `TxtIngestService.ingest_files()` and then index returned chunks in the same `sync_to_async(..., thread_sensitive=True)` sync unit.
  - Keep existing protocol output: send `ready` with `indexed_files` and `skipped_files`.
  - On `disconnect`, cancel/await background tasks, then call session index cleanup exactly once if present.
  - If ingest/indexing fails, clear chunks/index state and preserve current error behavior.

## Test Plan
- Install/update local dependencies:
  - `.venv/bin/python -m pip install -r backend/requirements.txt`
- Run Step 7 target:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_chroma.py -q`
- Run regressions:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py -q`
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ingest.py -q`
- Optional broader backend confidence pass:
  - `.venv/bin/python -m pytest backend/files/tests -q`

## Assumptions
- Step 7 scope is exactly the v02 TDD Build Plan row: ephemeral index implementation for the Step 6 tests.
- Retrieval and answer generation remain deferred to Steps 8-11.
- The local full Step 7 test run requires the new Chroma/LangChain packages and an available `OPENAI_API_KEY`; if dependency installation or network access is blocked, report that verification gap explicitly.
