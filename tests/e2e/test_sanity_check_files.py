"""Temporary sanity E2E checks for user-provided fixture files.

These tests are intended for manual verification with real sample files. Comment
out or delete this module after sanity verification is complete.
"""

from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from .client import FileVaultClient


SANITY_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sanity_check"
DOWNLOADS_DIR = SANITY_DIR / "downloads"
RATE_LIMIT_WAIT_SECONDS = 2.0 
#float(os.getenv("FILE_VAULT_RATE_LIMIT_WAIT", "1.1"))
SANITY_FILES = [
    SANITY_DIR / "AvePoint.pdf",
    SANITY_DIR / "Receipt.pdf",
    SANITY_DIR / "DestinyCockpitPilotView.png",
    SANITY_DIR / "FirstShot_Insta.mp4",
    SANITY_DIR / "ManDescription.txt",
]


def wait_for_rate_window() -> None:
    time.sleep(RATE_LIMIT_WAIT_SECONDS)


@pytest.fixture(scope="session", autouse=True)
def api_is_available() -> None:
    client = FileVaultClient(user_id="sanity-healthcheck")
    try:
        response = client.healthcheck()
    except requests.RequestException as exc:
        pytest.fail(
            f"File Vault API is unavailable at {client.base_url}. "
            "Start it with `docker compose up --build` before running sanity E2E tests. "
            f"Original error: {exc}",
            pytrace=False,
        )

    if response.status_code >= 500:
        pytest.fail(
            f"File Vault API at {client.base_url} returned {response.status_code}: {response.text}",
            pytrace=False,
        )


@pytest.fixture
def client() -> FileVaultClient:
    return FileVaultClient(user_id=f"sanity-{uuid4()}")


@pytest.fixture(autouse=True)
def downloads_dir() -> Path:
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    return DOWNLOADS_DIR


def content_type_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def upload_sanity_file(client: FileVaultClient, path: Path) -> dict:
    response = client.upload_file(path, content_type=content_type_for(path))
    assert response.status_code == 201, response.text
    return response.json()


def upload_all_sanity_files(client: FileVaultClient) -> list[dict]:
    uploaded = []
    for path in SANITY_FILES:
        uploaded.append(upload_sanity_file(client, path))
        wait_for_rate_window()
    return uploaded


def download_and_assert_matches(
    client: FileVaultClient,
    file_id: str,
    source_path: Path,
    downloads_dir: Path,
) -> Path:
    response = client.download_file(file_id)
    assert response.status_code == 200, response.text
    assert response.content == source_path.read_bytes()

    downloaded_path = downloads_dir / f"downloaded_{source_path.name}"
    downloaded_path.write_bytes(response.content)
    return downloaded_path


def payload_by_name(payloads: list[dict]) -> dict[str, dict]:
    return {payload["original_filename"]: payload for payload in payloads}


# Temporary manual sanity test: comment out or delete after verification.
def test_sanity_upload_and_download_all_provided_files(
    client: FileVaultClient,
    downloads_dir: Path,
) -> None:
    for path in SANITY_FILES:
        payload = upload_sanity_file(client, path)
        wait_for_rate_window()

        downloaded_path = download_and_assert_matches(
            client,
            payload["id"],
            path,
            downloads_dir,
        )

        assert downloaded_path.read_bytes() == path.read_bytes()
        wait_for_rate_window()


# Temporary manual sanity test: comment out or delete after verification.
def test_sanity_storage_savings_with_duplicate_upload(client: FileVaultClient) -> None:
    source_path = SANITY_DIR / "Receipt.pdf"
    original = upload_sanity_file(client, source_path)
    wait_for_rate_window()

    duplicate = upload_sanity_file(client, source_path)
    wait_for_rate_window()

    assert duplicate["is_reference"] is True
    assert duplicate["original_file"] == original["id"]
    assert duplicate["file_hash"] == original["file_hash"]

    response = client.storage_stats()
    print("Storage stats", response.json())
    assert response.status_code == 200, response.text
    stats = response.json()
    assert stats["storage_savings"] >= source_path.stat().st_size
    assert stats["savings_percentage"] > 0


# Temporary manual sanity test: comment out or delete after verification.
def test_sanity_search_and_filtering(client: FileVaultClient) -> None:
    uploaded = payload_by_name(upload_all_sanity_files(client))
    receipt = uploaded["Receipt.pdf"]
    png = uploaded["DestinyCockpitPilotView.png"]

    search_response = client.list_files(params={"search": "Receipt"})
    assert search_response.status_code == 200, search_response.text
    search_payload = search_response.json()
    assert search_payload["count"] == 1
    assert search_payload["results"][0]["id"] == receipt["id"]
    wait_for_rate_window()

    file_type_response = client.list_files(params={"file_type": png["file_type"]})
    assert file_type_response.status_code == 200, file_type_response.text
    file_type_payload = file_type_response.json()
    assert file_type_payload["count"] == 1
    assert file_type_payload["results"][0]["id"] == png["id"]
    assert all(item["file_type"] == png["file_type"] for item in file_type_payload["results"])
    wait_for_rate_window()

    small_files = [
        SANITY_DIR / "AvePoint.pdf",
        SANITY_DIR / "Receipt.pdf",
        SANITY_DIR / "ManDescription.txt",
    ]
    max_size = max(path.stat().st_size for path in small_files) + 1024
    size_response = client.list_files(params={"max_size": max_size})
    assert size_response.status_code == 200, size_response.text
    size_payload = size_response.json()
    result_names = {item["original_filename"] for item in size_payload["results"]}
    assert {"AvePoint.pdf", "Receipt.pdf", "ManDescription.txt"}.issubset(result_names)
    assert "DestinyCockpitPilotView.png" not in result_names
    assert "FirstShot_Insta.mp4" not in result_names


# Temporary manual sanity test: comment out or delete after verification.
def test_sanity_available_file_types(client: FileVaultClient) -> None:
    uploaded = upload_all_sanity_files(client)
    expected_file_types = sorted({payload["file_type"] for payload in uploaded})

    response = client.file_types()
    print("Returned file types:", response.json())
    assert response.status_code == 200, response.text
    assert response.json() == expected_file_types


# Temporary manual sanity test: comment out or delete after verification.
def test_sanity_delete_with_and_without_references(
    client: FileVaultClient,
    downloads_dir: Path,
) -> None:
    no_reference_path = SANITY_DIR / "ManDescription.txt"
    no_reference = upload_sanity_file(client, no_reference_path)
    wait_for_rate_window()

    delete_response = client.delete_file(no_reference["id"])
    assert delete_response.status_code == 204, delete_response.text
    wait_for_rate_window()

    retrieve_response = client.retrieve_file(no_reference["id"])
    assert retrieve_response.status_code == 404
    wait_for_rate_window()

    referenced_path = SANITY_DIR / "AvePoint.pdf"
    original = upload_sanity_file(client, referenced_path)
    wait_for_rate_window()

    reference = upload_sanity_file(client, referenced_path)
    assert reference["is_reference"] is True
    assert reference["original_file"] == original["id"]
    wait_for_rate_window()

    delete_original_response = client.delete_file(original["id"])
    assert delete_original_response.status_code == 204, delete_original_response.text
    wait_for_rate_window()

    download_and_assert_matches(
        client,
        reference["id"],
        referenced_path,
        downloads_dir,
    )
