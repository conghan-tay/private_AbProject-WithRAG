# Step 9: Delete Cascade and Reference-Aware Download

## Summary
Implement `DELETE /api/files/{id}/` so deletion preserves deduplication invariants, and update download resolution so duplicate reference rows download through their owning original. Current backend baseline is clean: `35 passed` for `backend/files/tests`.

## Key Changes
- Add delete lifecycle helpers in `DeduplicationService`:
  - `delete_reference(reference)`: atomically decrement the original’s `reference_count` and delete only the reference row.
  - `promote_reference(original_file)`: promote the oldest reference, preserve the physical file path and IV, and repoint remaining references.
  - `delete_original_file(original_file)`: delete the physical encrypted file and DB row only when no references remain.
- Override `FileViewSet.destroy()`:
  - use `get_object()` for per-user 404 isolation.
  - dispatch reference/original delete through `DeduplicationService`.
  - return `204 No Content`.
- Update `FileViewSet.download()` to resolve storage source:
  - if the requested row is an original, decrypt its own `file` and `encryption_iv`.
  - if the requested row is a reference, decrypt `record.original_file.file` using `record.original_file.encryption_iv`.
  - keep response filename and metadata semantics tied to the requested row, so duplicate references download with their own `original_filename`.

## Promotion Semantics
- Promotion must avoid the `(user_id, file_hash)` unique constraint for originals:
  - lock original and reference rows in `transaction.atomic()`.
  - capture original `file.name` and `encryption_iv`.
  - capture oldest reference and remaining reference IDs.
  - delete the old original DB row without deleting the physical file.
  - update the oldest reference to `is_reference=False`, `original_file=None`, copied `file`, copied `encryption_iv`, and `reference_count=old_count - 1`.
  - update remaining references to point at the promoted original.
- The promoted record keeps its own `original_filename`; only storage ownership moves.

## Test Plan
- Add backend tests for:
  - deleting a reference decrements the original count, removes the reference, and keeps the physical file.
  - deleting an original with references promotes the oldest reference, repoints remaining references, keeps one physical file, and makes the deleted original 404.
  - promoted original can still download decrypted bytes.
  - non-promoted reference rows download the original bytes using their own filename.
  - deleting an original with no references deletes the physical file, removes the DB row, and drops actual storage usage to zero.
  - cross-user delete still returns 404 through existing queryset scoping.
- Run `.venv/bin/python -m pytest backend/files/tests -q`.

## Assumptions
- No database migration is required.
- Quota “freed on delete” is implemented through correct physical delete and non-reference aggregate behavior, with actual quota rejection still left for Build Plan Step 11.
