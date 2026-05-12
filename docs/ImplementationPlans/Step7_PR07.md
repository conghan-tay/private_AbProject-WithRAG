# Step 7 DeduplicationService Implementation

## Summary
Implement per-user SHA-256 deduplication on the existing encrypted upload path. Duplicate uploads by the same `UserId` will create DB reference rows without writing new file bytes; identical uploads by different users remain independent originals. Race safety will be enforced by the database, not by app-level locking.

## Key Changes
- Add `files/services/dedup.py` with `DeduplicationService`:
  - `compute_hash(file_obj)`: stream in 8192-byte chunks, reset file pointer before/after.
  - `find_duplicate(user_id, file_hash)`: return the user’s non-reference original for that hash.
  - `create_reference(user_id, original, filename)`: atomically create a reference row with `file=None`, copy `file_type`, `size`, and `file_hash` from the original, set `original_filename` from the new upload, and increment the original’s `reference_count` with an `F()` update.
- Update `File.Meta.constraints` and add migration `0002`:
  - Add conditional unique constraint on `["user_id", "file_hash"]` where `is_reference=False`.
  - Constraint name: `files_file_unique_original_hash_per_user`.
- Refactor `FileViewSet.create()`:
  - Use `DeduplicationService.compute_hash()` instead of the inline view helper.
  - Run duplicate lookup before encryption/write.
  - If duplicate exists, call `create_reference()` and return serialized metadata with `201`.
  - If no duplicate exists, keep the current encrypt-and-save original flow.
  - Catch `IntegrityError` from original creation, delete any orphaned ciphertext file written before the failed insert, re-fetch the winning original, and create a reference instead.
- Keep encryption behavior unchanged:
  - Originals are encrypted before disk write.
  - References do not write bytes and keep `encryption_iv=None`.
  - Download behavior for references is out of scope for Step 7 unless already requested later; Step 9 delete/promotion will handle reference lifecycle.

## Test Plan
- Add `backend/files/tests/test_dedup.py` covering:
  - `compute_hash()` returns SHA-256 and resets the file pointer.
  - `find_duplicate()` only finds same-user originals, not references or other users’ files.
  - First upload creates `is_reference=False`, `reference_count=1`, and writes one encrypted file.
  - Second identical upload by same user creates `is_reference=True`, `original_file=<original id>`, same hash/size/type, no physical file, and increments original `reference_count` to `2`.
  - Third identical upload by same user creates another reference and increments original `reference_count` to `3`.
  - Same bytes uploaded by another user create a separate original.
  - Simulated unique-constraint race: force original insert to raise `IntegrityError` after lookup misses, then assert the upload resolves to a reference and cleans up the orphaned file.
- Update `test_models.py`:
  - Replace the Step 3 “no dedup unique constraint” assertion with the Step 7 conditional unique constraint assertion.
- Run:
  - `.venv/bin/python -m pytest backend/files/tests -q`

## Assumptions
- The unique constraint is conditional for originals only, because literal uniqueness across all rows would prevent multiple reference rows for repeated duplicate uploads.
- References retain the same `file_hash` as their original for metadata consistency and future storage stats.
- Step 7 does not implement delete cascade, storage stats, quota checks, filtering, or throttling.
