# Migrate Persistence Layer from SQLite to PostgreSQL

## Summary
Replace SQLite with PostgreSQL 16 as the metadata store. The schema, ORM queries, and the 8 indexes on the `File` model are already portable, so this is a pure infrastructure and configuration swap with no application code changes. No data migration is required because the SQLite database in the repository is empty; the new Postgres database is provisioned fresh on first startup and Django migrations 0001 and 0002 are applied automatically.

## Key Changes
- `backend/core/settings.py`: replace the hardcoded SQLite `DATABASES` block with `dj_database_url.config(default=DATABASE_URL, conn_max_age=600, conn_health_checks=True)`. Default `DATABASE_URL` to a local Postgres URL so non-Docker `manage.py` commands stay ergonomic.
- `backend/requirements.txt`: add `psycopg[binary]==3.2.3` and `dj-database-url==2.3.0`. `psycopg` v3 bundles libpq through the binary wheel, so no Dockerfile system packages are added.
- `docker-compose.yml`: add a `postgres:16-alpine` service with named volume `postgres_data` and a `pg_isready` healthcheck. Rewire the `backend` service to `depends_on: { postgres: { condition: service_healthy } }` and inject `DATABASE_URL=postgres://filevault:filevault@postgres:5432/filevault`. Remove the obsolete `backend_data` named volume and its `/app/data` mount on backend.
- `backend/start.sh`: drop `mkdir -p /app/data` and `python manage.py makemigrations` (production should never auto-generate migrations). Add a 30-second connection-retry loop using `psycopg.connect(..., connect_timeout=2)` before `python manage.py migrate --noinput`.
- `backend/Dockerfile`: drop `data` from the directory creation and `chmod` lines now that SQLite is gone.
- `.gitignore`: keep `backend/data/` and add `*.sqlite3`.
- Filesystem cleanup: delete the empty `backend/data/db.sqlite3` and the `backend/data/` directory.
- `README.md`: update the Architecture overview "Persistence" line, the Docker setup paragraph, the "Fresh data reset" section, the Configuration reference table (new `DATABASE_URL` row), the Local Python setup section (note that non-Docker dev requires a reachable Postgres), the service-boundaries paragraph in Implementation notes, and the Project status block.

## Public Interfaces
- No HTTP API changes.
- New runtime environment variable: `DATABASE_URL`. Set by Docker Compose; defaults in `settings.py` to `postgres://filevault:filevault@localhost:5432/filevault` for local non-Docker use.
- Docker Compose now exposes three services: `backend`, `redis`, `postgres`. PostgreSQL is not published to a host port; access it with `docker compose exec postgres psql -U filevault -d filevault`.
- The `backend_data` named volume is removed; `postgres_data` is added. `docker compose down -v` now wipes the Postgres data directory instead of the SQLite file.

## Test Plan
1. `docker compose down -v && docker compose up --build`.
2. `docker compose ps` reports `postgres` as `healthy`; the backend log shows `Waiting for database...` followed by `Running migrations...` and all `files` migrations marked `OK`.
3. `docker compose exec postgres psql -U filevault -d filevault -c '\dt'` lists `files_file`, `django_migrations`, and the standard Django tables. `\di` shows the 8 indexes from the `File` model plus the partial unique index from migration 0002.
4. Smoke check: `curl -H "UserId: local-dev" http://localhost:8000/api/files/` returns `{"count":0,...}`.
5. Manual upload, duplicate upload, download, delete sequence using the curl examples in the README. Verifies upload, dedup (the partial unique constraint on `(user_id, file_hash) WHERE NOT is_reference`), download streaming, and delete cascade.
6. `.venv/bin/python -m pytest tests/e2e -q` against the running compose stack.
7. `.venv/bin/python -m pytest backend/files/tests -q` with a local Postgres reachable through the default `DATABASE_URL` (or with `DATABASE_URL` overridden to a throwaway database).

## Assumptions
- Fresh database. The 0-byte SQLite file is discarded and no migration of existing rows is required.
- Driver: `psycopg` v3 binary wheel. Django 4.2.30 auto-detects v3 when only it is installed; no explicit `ENGINE` override is needed.
- Configuration: a single `DATABASE_URL` string via `dj-database-url`. Matches the existing `REDIS_URL` convention.
- Migrations 0001 and 0002 are portable. Migration 0002's `UniqueConstraint(condition=Q(is_reference=False), fields=("user_id","file_hash"))` renders on Postgres as `CREATE UNIQUE INDEX ... WHERE NOT is_reference`. Verified against the model and migration code.
- Postgres is not published to a host port. Devs use `docker compose exec postgres psql ...` for inspection.
- `conn_max_age=600` plus `conn_health_checks=True` keep Gunicorn sync workers resilient to brief Postgres restarts without leaking stale connections.
- Local non-Docker development requires a reachable PostgreSQL at the default URL or an explicit `DATABASE_URL` override. Documented in the README.
