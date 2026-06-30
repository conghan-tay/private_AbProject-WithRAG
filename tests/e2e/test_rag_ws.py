"""Docker-backed Ask the Vault WebSocket E2E smoke test."""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlencode
from uuid import uuid4

import pytest
import requests
import websockets

from .client import FileVaultClient


RUN_RAG_E2E = os.getenv("RUN_ASKVAULT_RAG_E2E") == "1"
DEFAULT_RAG_WS_URL = "ws://localhost:8001/ws/ask-vault/"

pytestmark = pytest.mark.skipif(
    not RUN_RAG_E2E,
    reason="Set RUN_ASKVAULT_RAG_E2E=1 to run the Docker Ask the Vault E2E smoke.",
)


@pytest.fixture(scope="module", autouse=True)
def api_is_available() -> None:
    client = FileVaultClient(user_id="rag-e2e-healthcheck")
    try:
        response = client.healthcheck()
    except requests.RequestException as exc:
        pytest.fail(
            f"File Vault API is unavailable at {client.base_url}. "
            "Start it with `ASKVAULT_RAG_E2E_FAKE=True docker compose up --build` "
            "before running this test. "
            f"Original error: {exc}",
            pytrace=False,
        )

    if response.status_code >= 500:
        pytest.fail(
            f"File Vault API at {client.base_url} returned "
            f"{response.status_code}: {response.text}",
            pytrace=False,
        )


def rag_ws_url(user_id: str) -> str:
    base_url = os.getenv("FILE_VAULT_RAG_WS_URL", DEFAULT_RAG_WS_URL)
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode({'user_id': user_id})}"


async def receive_json(websocket, timeout=30.0):
    raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    return json.loads(raw_message)


def test_upload_select_ask_streams_tokens_and_sources() -> None:
    asyncio.run(assert_upload_select_ask_streams_tokens_and_sources())


async def assert_upload_select_ask_streams_tokens_and_sources() -> None:
    unique = uuid4().hex
    user_id = f"rag-e2e-{unique}"
    marker = f"askvault marker {unique} answer code bluebird"
    file_text = (
        f"{marker}\n"
        "The selected incident note says the analyst should cite this file."
    )
    client = FileVaultClient(user_id=user_id, timeout=10.0)

    upload_response = client.upload_bytes(
        f"askvault-{unique}.txt",
        file_text.encode("utf-8"),
        content_type="text/plain",
    )
    assert upload_response.status_code == 201, upload_response.text
    uploaded = upload_response.json()
    assert uploaded["file_type"] == "text/plain"

    async with websockets.connect(rag_ws_url(user_id), open_timeout=10) as websocket:
        assert await receive_json(websocket) == {
            "type": "status",
            "state": "connected_no_documents",
        }

        await websocket.send(
            json.dumps(
                {
                    "action": "select",
                    "file_ids": [uploaded["id"]],
                }
            )
        )
        assert await receive_json(websocket) == {
            "type": "status",
            "state": "ingesting",
        }
        assert await receive_json(websocket) == {
            "type": "ready",
            "indexed_files": 1,
            "skipped_files": [],
        }

        await websocket.send(
            json.dumps(
                {
                    "action": "ask",
                    "question": file_text,
                }
            )
        )

        tokens = []
        while True:
            message = await receive_json(websocket)
            if message["type"] == "token":
                tokens.append(message["data"])
                continue
            if message["type"] == "done":
                assert tokens
                assert message["sources"] == [uploaded["id"]]
                break
            pytest.fail(f"Unexpected Ask the Vault message: {message}")
