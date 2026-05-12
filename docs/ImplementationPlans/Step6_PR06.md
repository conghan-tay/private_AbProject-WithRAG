# Step 6: Encryption At Rest

## Summary
Implement AES-GCM encryption for uploaded file bytes, persist the per-file nonce in `File.encryption_iv`, and add a download endpoint that decrypts stored ciphertext back to the original plaintext. Keep Step 6 scoped to encryption/download only; deduplication, quotas, throttling, filtering, and delete cascade remain later steps.

## Implementation Changes
- Add `backend/files/services/encryption.py` and `backend/files/services/__init__.py`.
- Implement `EncryptionService` with:
  - `encrypt_file(file_obj) -> tuple[bytes, bytes]`
  - `decrypt_file(ciphertext: bytes, iv: bytes) -> bytes`
  - AES-256-GCM via `cryptography.hazmat.primitives.ciphers.aead.AESGCM`.
  - Generate a 12-byte AES-GCM nonce with `os.urandom(12)` and store it in existing `encryption_iv` field; the field’s `max_length=16` remains compatible.
  - Store ciphertext including the GCM auth tag, as returned by `AESGCM.encrypt()`.
- Add key loading:
  - If `ENCRYPTION_KEY` is set, treat it as a base64-encoded 32-byte key and validate length.
  - If unset and `DEBUG=True`, derive a deterministic local-only 32-byte key from `SECRET_KEY`.
  - If unset and `DEBUG=False`, raise `ImproperlyConfigured`.
- Update `backend/files/views.py`:
  - Keep existing Step 5 hash/MIME behavior.
  - On upload, encrypt the validated uploaded file before saving.
  - Save ciphertext through Django storage using `ContentFile`, not the raw upload object.
  - Persist `encryption_iv=iv`.
  - Add a DRF `@action(detail=True, methods=["get"])` named `download`.
  - Scope download lookup through `get_queryset()` so cross-user IDs return 404.
  - Read stored ciphertext, decrypt through `EncryptionService`, and return `StreamingHttpResponse` with `Content-Disposition: attachment; filename="<original_filename>"`.
- Update serializer output only if needed:
  - Keep `encryption_iv` out of API responses.
  - Preserve the existing Step 5 metadata contract.

## Test Plan
- Add `backend/files/tests/test_encryption.py` covering:
  - `EncryptionService.encrypt_file()` returns ciphertext different from plaintext and a stored nonce.
  - `decrypt_file()` round-trips to original bytes.
  - Upload writes ciphertext to disk, not plaintext.
  - Uploaded record has `encryption_iv` populated.
  - `GET /api/files/{id}/download/` returns plaintext bytes and attachment headers.
  - Download by another `UserId` returns 404.
- Update the existing Step 5 upload test that currently expects raw disk bytes to equal plaintext; it should now assert disk bytes differ from plaintext.
- Run:
  - `.venv/bin/python -m pytest backend/files/tests -q`
  - Optionally run the E2E download test once later steps make rate limiting and final endpoints stable.

## Assumptions
- AES-GCM is the selected encryption mode.
- `encryption_iv` stores the AES-GCM nonce, despite the field name using “IV”.
- Local development may use the DEBUG fallback key; production must configure `ENCRYPTION_KEY`.
- Step 6 does not introduce deduplication references, quota checks, rate limiting, or storage stats.
