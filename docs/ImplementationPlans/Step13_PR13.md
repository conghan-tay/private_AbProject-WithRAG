# Step 13 Edge Case Hardening

## Summary
Implement Step 13 as focused regression coverage for the remaining edge cases only: zero-byte files, MIME spoofing, SQL injection search safety, and race-condition constraint handling. No cross-user 404 changes are planned because that behavior is already implemented through scoped querysets and covered by earlier tests.

## Key Changes
- Add `backend/files/tests/test_edge_cases.py` with focused API/service tests:
  - zero-byte upload succeeds with `size=0`, SHA-256 empty digest, encrypted stored file, empty download body, and duplicate zero-byte upload becomes a reference
  - misleading extension upload, such as JPEG-signature bytes named `spoof.txt`, is classified by content rather than request `content_type` or filename extension
  - SQL-injection-like `search` values return `200`, remain scoped to the requesting user, and do not broaden results
  - unique-constraint race fallback still produces one original plus one reference and cleans up orphaned ciphertext
- Keep implementation changes minimal:
  - update MIME fallback only if zero-byte or spoof tests reveal an unstable falsey `magic.from_buffer()` result
  - do not alter serializer fields, route names, database schema, quota behavior, or rate-limit behavior

## Public API / Interfaces
- No new endpoints or response fields.
- Existing guarantees become explicit:
  - `POST /api/files/` accepts zero-byte files
  - MIME type is content-detected
  - search input is handled safely through ORM filtering
  - concurrent same-user duplicate uploads resolve to one original record

## Test Plan
- Run targeted tests: `.venv/bin/python -m pytest backend/files/tests/test_edge_cases.py -q`
- Run full backend suite: `.venv/bin/python -m pytest backend/files/tests -q`
- Expected final state: all existing `58` tests still pass, plus new Step 13 edge-case tests.

## Assumptions
- Cross-user retrieve/download/delete 404 is already satisfied and intentionally left out of this step.
- SQL injection safety is validated behaviorally through malicious search strings and user scoping, not by inspecting raw SQL.
- MIME spoofing assertions should prove “not extension/request-header driven” without overfitting to platform-specific libmagic labels.
