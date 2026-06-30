#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REST_URL="http://localhost:8000/api/files/"
WS_HOST="localhost"
WS_PORT=8001
WS_PATH="/ws/ask-vault/?user_id=local-dev"
WS_MISSING_USER_PATH="/ws/ask-vault/"
WAIT_SECONDS=90
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON:-python}"
fi

cleanup() {
  local status=$?
  trap - EXIT

  echo
  echo "Tearing down Docker Compose stack and volumes..."
  docker compose down -v || true

  exit "$status"
}

trap cleanup EXIT

export ASKVAULT_RAG_E2E_FAKE=True

echo "Starting Docker Compose stack..."
docker compose up --build -d

echo "Waiting for REST API at ${REST_URL}..."
REST_URL="$REST_URL" WAIT_SECONDS="$WAIT_SECONDS" python - <<'PY'
import os
import sys
import time
import urllib.error
import urllib.request

url = os.environ["REST_URL"]
wait_seconds = int(os.environ["WAIT_SECONDS"])
deadline = time.time() + wait_seconds
last_error = "not attempted"

while time.time() < deadline:
    request = urllib.request.Request(url, headers={"UserId": "local-dev"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            body = response.read(200)
            if response.status == 200:
                print(f"REST OK: HTTP {response.status}")
                sys.exit(0)
            last_error = f"HTTP {response.status}: {body!r}"
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        last_error = repr(exc)

    time.sleep(2)

print(f"REST smoke failed after {wait_seconds}s: {last_error}", file=sys.stderr)
sys.exit(1)
PY

echo "Checking WebSocket upgrade at ws://${WS_HOST}:${WS_PORT}${WS_PATH}..."
WS_HOST="$WS_HOST" WS_PORT="$WS_PORT" WS_PATH="$WS_PATH" WS_MISSING_USER_PATH="$WS_MISSING_USER_PATH" python - <<'PY'
import base64
import os
import socket
import sys

host = os.environ["WS_HOST"]
port = int(os.environ["WS_PORT"])
valid_path = os.environ["WS_PATH"]
missing_user_path = os.environ["WS_MISSING_USER_PATH"]


def websocket_response(path):
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )

    with socket.create_connection((host, port), timeout=5) as sock:
        sock.sendall(request.encode("ascii"))
        return sock.recv(4096).decode("latin1").split("\r\n\r\n", 1)[0]


valid_response = websocket_response(valid_path)
print(valid_response)
if not valid_response.startswith("HTTP/1.1 101 Switching Protocols"):
    print("WebSocket upgrade smoke failed: expected 101 Switching Protocols", file=sys.stderr)
    sys.exit(1)

missing_user_response = websocket_response(missing_user_path)
print(missing_user_response)
if not missing_user_response.startswith("HTTP/1.1 403 Forbidden"):
    print("WebSocket rejection smoke failed: expected 403 Forbidden", file=sys.stderr)
    sys.exit(1)

print("WebSocket OK: accepted valid user_id and rejected missing user_id")
PY

echo "Docker runtime smoke checks passed."

echo "Running Ask the Vault RAG E2E smoke..."
RUN_ASKVAULT_RAG_E2E=1 "$PYTHON_BIN" -m pytest tests/e2e/test_rag_ws.py -q

echo "Ask the Vault RAG E2E smoke passed."
