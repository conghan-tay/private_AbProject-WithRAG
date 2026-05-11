# Step 3: File Data Model and Index Migration

## Summary
Implement the PRD Section 5.1 `File` entity and Section 5.2 database indexes in the Django `files` app. This step updates only the model, creates the initial migration, and adds model/schema tests; deduplication behavior and unique race-condition constraints remain deferred to Step 7.

## Key Changes
- Update `backend/files/models.py` so `File` contains:
  `id`, `user_id`, `file`, `original_filename`, `file_type`, `size`, `file_hash`, `is_reference`, `original_file`, `reference_count`, `uploaded_at`, and `encryption_iv`.
- Keep `file_upload_path()` and `Meta.ordering = ["-uploaded_at"]`.
- Make `file` nullable/blank for reference records, `original_file` nullable/blank with `on_delete=models.SET_NULL`, and `encryption_iv` nullable/blank with `max_length=16`.
- Define all required indexes in `File.Meta.indexes` with stable names:
  `files_file_user_id`, `files_file_file_type`, `files_file_size`, `files_file_uploaded_at`, `files_file_file_hash`, `files_file_original_filename`, `files_file_user_uploaded_at`, and `files_file_user_is_reference`.
- Create `backend/files/migrations/0001_initial.py` for the complete model because the repo currently has no committed `files` migrations.

## Public Interfaces
- Serializer/API changes are limited to making the new fields available later; Step 3 does not implement upload, deduplication, filtering, middleware, or quota behavior.
- No unique constraint is added in this step. Step 7 should add a conditional uniqueness constraint for originals only: `user_id + file_hash` where `is_reference=False`.

## Test Plan
- Add focused model tests that assert:
  field types and max lengths match PRD Section 5.1;
  nullable fields are exactly `file`, `original_file`, and `encryption_iv`;
  defaults are `is_reference=False` and `reference_count=1`;
  `uploaded_at` uses `auto_now_add`;
  all Section 5.2 index names and field sets are present.
- Verify with:
  `python backend/manage.py check`
  `python backend/manage.py makemigrations --check --dry-run`
  `python -m pytest` or the project’s available test command.
- For local DB-backed checks, ensure `backend/data/` exists first; Docker startup already creates `/app/data`.

## Assumptions
- The Step 3 scope is model + migration + schema tests only.
- Index coverage should come from explicit `Meta.indexes`, not duplicate `db_index=True` field indexes.
- Existing E2E tests are expected to keep failing until later build-plan steps implement API behavior.
