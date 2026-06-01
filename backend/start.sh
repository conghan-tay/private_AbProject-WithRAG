#!/bin/sh

echo "Waiting for database..."
python - <<'PY'
import os
import sys
import time

import psycopg

url = os.environ["DATABASE_URL"]
for _ in range(30):
    try:
        with psycopg.connect(url, connect_timeout=2):
            sys.exit(0)
    except Exception:
        time.sleep(1)
sys.exit("Database unreachable after 30s")
PY

echo "Running migrations..."
python manage.py migrate --noinput

GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
echo "Starting server with ${GUNICORN_WORKERS} Gunicorn worker(s)..."
gunicorn --workers "${GUNICORN_WORKERS}" --bind 0.0.0.0:8000 core.wsgi:application
