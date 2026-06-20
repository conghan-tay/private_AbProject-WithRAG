# Step 5: TXT Ingest Implementation

## Summary
Implement the session-scoped TXT ingest service for Ask the Vault. This step makes the existing Step 4 ingest tests pass by decrypting selected owned `text/plain` vault files, decoding UTF-8, splitting text into chunks, and returning chunk metadata plus skipped-file reasons. It will not add Chroma, embeddings, retrieval, or LLM streaming.

## Key Changes
- Add `files.services.rag_ingest.TxtIngestService` with:
  - `ingest_files(user_id, file_ids, text_splitter=None) -> {"indexed_files": int, "skipped_files": list, "chunks": list}`.
  - De-duplicate repeated `file_ids` while preserving first-seen order.
  - Fetch only `File` rows matching both selected IDs and `user_id`, using `select_related("original_file")`.
  - Report missing and cross-user IDs uniformly as `not_found_or_not_owned`.
- Add only the Step 5 splitter dependency:
  - `langchain-text-splitters==1.1.2`
  - Add `RAG_CHUNK_SIZE` and `RAG_CHUNK_OVERLAP` settings.
  - Lazily create `RecursiveCharacterTextSplitter` only when `text_splitter` is not supplied.
  - Do not add `langchain`, `langchain-openai`, `langchain-chroma`, or `chromadb` in Step 5.
- Implement TXT handling:
  - Skip owned non-`text/plain` files as `unsupported_type`, including `file_type`.
  - Resolve storage via `record.original_file if record.is_reference else record`.
  - Skip missing storage record, file path, or IV as `malformed_storage`.
  - Decrypt with `EncryptionService.decrypt_file_stream(...)`.
  - Decode strictly as UTF-8; skip decode failures as `unsupported_encoding`.
  - Split with the supplied fake splitter in tests or the lazy LangChain splitter in real use.
  - If no chunks are produced, skip as `no_chunks`.
- Return chunk objects shaped as:
  - `{"page_content": chunk_text, "metadata": {...}}`
  - metadata contains `user_id`, selected logical `file_id`, resolved `storage_file_id`, selected record `original_filename`, `file_type`, and sequential `chunk_index`.
- Wire `AskVaultConsumer.run_ingest()` to call `TxtIngestService.ingest_files()` through `sync_to_async(..., thread_sensitive=True)`.
  - Preserve returned `chunks` on the consumer instance for later Step 7 indexing.
  - Keep the current socket response as `ready` with `indexed_files` and `skipped_files`.

## Test Plan
- Install the new splitter dependency if missing:
  - `.venv/bin/python -m pip install -r backend/requirements.txt`
- Run Step 5 target:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ingest.py -q`
- Run protocol regression:
  - `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py -q`
- Run encryption regression:
  - `.venv/bin/python -m pytest backend/files/tests/test_encryption.py -q`

## Assumptions
- Step 5 is the implementation for the existing Step 4 tests.
- Vector indexing and OpenAI embedding calls remain out of scope until Step 7.
- Selected logical file IDs remain the source metadata, even when bytes come from a deduplicated original.
- Skipped unowned files must not reveal whether the file exists for another user.
