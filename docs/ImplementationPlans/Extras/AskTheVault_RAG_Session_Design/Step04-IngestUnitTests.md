# Step 4: Ask The Vault Ingest Unit Tests

## Summary
Add tests-only coverage for the future TXT ingest service described in `AskTheVault_RAG_Session_Design_v02.md`. These tests should fail until Step 5 creates the service and wires the ingest behavior.

## Key Changes
- Add `backend/files/tests/test_rag_ingest.py`.
- Define the expected service contract as `files.services.rag_ingest.TxtIngestService.ingest_files(user_id, file_ids, text_splitter=None)`.
- Expected return shape:
  ```python
  {
      "indexed_files": int,
      "skipped_files": [{"file_id": str, "reason": str, ...}],
      "chunks": [{"page_content": str, "metadata": dict}],
  }
  ```
- Use a fake splitter in tests so Step 4 does not require LangChain/Chroma/OpenAI dependencies yet.
- Build encrypted test records with the existing `EncryptionService.encrypt_file_to_temp`, then assert future ingest uses `decrypt_file_stream` semantics rather than plaintext shortcuts.

## Test Cases
- Owned `text/plain` file:
  - decrypts, decodes UTF-8, splits text, returns chunks, and reports `indexed_files == 1`.
  - chunk metadata includes `user_id`, selected logical `file_id`, `storage_file_id`, `original_filename`, `file_type`, and `chunk_index`.
- Missing file ID and cross-user file ID:
  - skipped as `not_found_or_not_owned`.
  - do not leak whether another user’s file exists.
- Unsupported MIME type:
  - owned non-`text/plain` records are skipped as `unsupported_type`.
  - skip entry includes the actual `file_type`.
- Invalid UTF-8 TXT:
  - skipped as `unsupported_encoding`.
  - no lossy replacement or silent mutation is allowed.
- Dedup reference:
  - selected reference row resolves encrypted bytes and IV from `original_file`.
  - metadata keeps the selected reference ID as `file_id`.
  - metadata uses the original/promoted storage record ID as `storage_file_id`.
- Malformed storage:
  - reference without a usable original/storage file is skipped as `malformed_storage`.

## Verification
- Run the new targeted test module:
  `.venv/bin/python -m pytest backend/files/tests/test_rag_ingest.py -q`
- Expected Step 4 result: tests fail because `files.services.rag_ingest.TxtIngestService` does not exist yet.
- Run existing protocol tests to ensure the tests-only addition does not alter Step 1-3 behavior:
  `.venv/bin/python -m pytest backend/files/tests/test_rag_ws_protocol.py -q`

## Assumptions
- Step 4 is intentionally tests-only per the v02 build plan.
- No production code, requirements, settings, Docker, or consumer behavior changes belong in Step 4.
- `CLAUDE.md` remains ignored.
