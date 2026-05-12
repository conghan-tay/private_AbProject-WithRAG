# Sanity E2E Tests With Provided Files

## Summary
Add a separate E2E sanity test module for the files in `tests/fixtures/sanity_check`. These tests are intentionally manual verification helpers and will include comments noting they should be commented out or deleted after verification.

## Key Changes
- Add `tests/e2e/test_sanity_check_files.py`.
- Use the existing `FileVaultClient` and rate-limit wait helper pattern.
- Use files from `tests/fixtures/sanity_check`:
  - `AvePoint.pdf`
  - `Receipt.pdf`
  - `DestinyCockpitPilotView.png`
  - `FirstShot_Insta.mp4`
  - `ManDescription.txt`
- Create `tests/fixtures/sanity_check/downloads/` at test runtime.
- Write downloaded files into that folder using deterministic names like `downloaded_<original_filename>`.
- Add a short module-level comment and test comments stating these sanity tests are temporary and should be commented out or deleted after verification.
- Add `tests/fixtures/sanity_check/downloads/.gitignore` with downloaded artifacts ignored, so running sanity tests does not accidentally add binary output files.

## Test Scenarios
- Upload/download:
  - Upload each sanity file.
  - Download each file.
  - Save downloaded bytes to `sanity_check/downloads/`.
  - Assert downloaded bytes exactly match the original file bytes.
- Storage savings:
  - Upload one file, then upload the same file again.
  - Assert second upload is `is_reference=True`.
  - Assert `storage_stats.storage_savings > 0` and `savings_percentage > 0`.
- Search and filtering:
  - Upload all sanity files under one unique sanity user.
  - Assert filename search finds expected files.
  - Assert `file_type` filter returns only records with that MIME type.
  - Assert size range filter returns only files in the requested byte range.
- Available file types:
  - Assert `/file_types/` returns distinct MIME types for the sanity user.
  - Do not hardcode MP4 as `video/mp4`; accept actual content-detected type such as `video/quicktime`.
- Delete behavior:
  - Without references: upload a file, delete it, assert later retrieve returns `404`.
  - With references: upload duplicate, delete original, assert duplicate remains downloadable with matching bytes.

## Public API / Interfaces
- No backend API changes.
- No changes to existing E2E client required unless a small helper is useful for writing downloads.
- Tests remain requests-based and require the API to be running, same as existing E2E tests.

## Assumptions
- Total fixture size is under the `10 MB` per-user quota, so all sanity files can be uploaded by one user.
- Downloaded files are local verification artifacts and should not be committed.
- These tests are meant as manual confidence checks, not permanent core regression coverage.
