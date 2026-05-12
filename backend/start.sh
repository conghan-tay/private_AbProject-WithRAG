#!/bin/sh

# Ensure data directory exists and has proper permissions
mkdir -p /app/data
chmod -R 777 /app/data

# Run migrations
echo "Running migrations..."
python manage.py makemigrations
python manage.py migrate

# Start server
GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
echo "Starting server with ${GUNICORN_WORKERS} Gunicorn worker(s)..."
gunicorn --workers "${GUNICORN_WORKERS}" --bind 0.0.0.0:8000 core.wsgi:application
