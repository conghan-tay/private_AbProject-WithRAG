# Chunked Encryption and Streaming Download

## Summary
Refactor encryption and download to avoid full-file buffering while keeping the public API unchanged. Use one internal chunked AES-GCM format only: no magic header and no backward-compatible decrypt path for previously uploaded one-shot ciphertext.

## Key Changes
- Add chunked AES-GCM methods in `EncryptionService`:
  - `encrypt_file_to_temp(file_obj) -> (temp_file, iv)` encrypts plaintext chunks into a temporary encrypted file and rewinds it.
  - `decrypt_file_stream(encrypted_file_obj, iv, original_size) -> iterator[bytes]` yields decrypted plaintext chunks.
  - Keep `encryption_iv` as the per-file 12-byte base nonce; no migration.
- Define deterministic chunk format:
  - Default plaintext chunk size: `ENCRYPTION_CHUNK_SIZE_BYTES = 1024 * 1024`.
  - Each chunk is encrypted independently with AES-GCM.
  - Chunk nonce is derived from base nonce plus chunk index.
  - Chunk index is used as authenticated additional data.
  - Full encrypted chunks are `plaintext_chunk_size + 16` bytes because AES-GCM appends a 16-byte tag.
  - Final encrypted chunk size is `(remaining_plaintext_bytes + 16)`.
  - Zero-byte files are stored as one encrypted empty chunk with ciphertext length `16`.
- Update upload:
  - Keep existing order: validate, MIME sample, hash, dedup lookup, quota check.
  - For new originals, call `encrypt_file_to_temp(file_obj)`.
  - Save the encrypted temp file through `record.file.save(...)`.
  - Close the temp file after storage save.
  - Preserve existing dedup race cleanup behavior.
- Update download:
  - Resolve references to their original storage record as today.
  - Return `StreamingHttpResponse(EncryptionService.decrypt_file_stream(...))`.
  - Open and close the encrypted file inside the generator so response iteration owns file lifetime.
  - Keep existing `Content-Type` and `Content-Disposition` behavior.

## Public API / Interfaces
- No endpoint, serializer, model, route, or response shape changes.
- Add one optional setting:
  - `ENCRYPTION_CHUNK_SIZE_BYTES = int(os.environ.get("ENCRYPTION_CHUNK_SIZE_BYTES", 1024 * 1024))`
- Existing local files encrypted with the old one-shot format will no longer be readable after this refactor. This is accepted for the take-home.

## Test Plan
- Update encryption service tests:
  - round trip for data larger than one chunk using a small overridden chunk size
  - encrypted output does not contain plaintext and is non-empty for zero-byte input
  - chunked encryption consumes `file_obj.chunks()` rather than full `.read()`
  - corrupted encrypted bytes raise a decryption error
- Update upload/download integration tests:
  - upload writes ciphertext and stores a 12-byte IV
  - download returns exact original plaintext
  - reference download streams from original storage but keeps reference filename/content type
  - zero-byte upload/download still passes
- Run:
  - `.venv/bin/python -m pytest backend/files/tests/test_encryption.py -q`
  - `.venv/bin/python -m pytest backend/files/tests/test_edge_cases.py -q`
  - `.venv/bin/python -m pytest backend/files/tests -q`

## Assumptions
- Existing persisted media/database state can be discarded or re-uploaded after this internal format change.
- `1 MB` chunks are sufficient for this challenge’s `10 MB` file limit.
- Direct-to-final-storage streaming is out of scope; Django already streams multipart uploads to temp files, and this refactor removes the remaining full-buffer encryption/download behavior.
