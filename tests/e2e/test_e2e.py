"""E2E progress dashboard for the final File Vault API contract.

These tests intentionally describe the completed PRD behavior. At Build Plan
Step 2 they should run cleanly but fail until later implementation steps land.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from .client import FileVaultClient


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
RATE_LIMIT_WAIT_SECONDS = float(os.getenv("FILE_VAULT_RATE_LIMIT_WAIT", "1.1"))


def wait_for_rate_window() -> None:
    time.sleep(RATE_LIMIT_WAIT_SECONDS)


@pytest.fixture(scope="session", autouse=True)
def api_is_available() -> None:
    client = FileVaultClient(user_id="e2e-healthcheck")
    try:
        response = client.healthcheck()
    except requests.RequestException as exc:
        pytest.fail(
            f"File Vault API is unavailable at {client.base_url}. "
            "Start it with `docker compose up --build` before running E2E tests. "
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
    return FileVaultClient(user_id=f"e2e-{uuid4()}")


def sample_pdf_path() -> Path:
    return FIXTURES_DIR / "sample.pdf"


def assert_file_metadata_contract(payload: dict) -> None:
    expected_fields = {
        "id",
        "user_id",
        "original_filename",
        "file_type",
        "size",
        "file_hash",
        "is_reference",
        "original_file",
        "reference_count",
        "uploaded_at",
    }
    assert expected_fields.issubset(payload.keys())


def upload_pdf(client: FileVaultClient) -> dict:
    response = client.upload_file(sample_pdf_path(), content_type="application/pdf")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert_file_metadata_contract(payload)
    return payload


def test_upload_file_returns_final_metadata_contract(client: FileVaultClient) -> None:
    payload = upload_pdf(client)

    assert payload["original_filename"] == "sample.pdf"
    assert payload["file_type"] == "application/pdf"
    assert payload["size"] == sample_pdf_path().stat().st_size
    assert payload["is_reference"] is False
    assert payload["reference_count"] == 1
    assert len(payload["file_hash"]) == 64


def test_list_files_contains_uploaded_file_in_paginated_envelope(client: FileVaultClient) -> None:
    uploaded = upload_pdf(client)
    wait_for_rate_window()

    response = client.list_files()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert {"count", "next", "previous", "results"}.issubset(payload.keys())
    assert any(item["id"] == uploaded["id"] for item in payload["results"])


def test_search_query_filters_files_by_filename(client: FileVaultClient) -> None:
    unique = uuid4().hex
    matching_name = f"incident-report-{unique}.txt"
    other_name = f"notes-{unique}.txt"

    matching_response = client.upload_bytes(matching_name, f"incident {unique}".encode())
    assert matching_response.status_code == 201, matching_response.text
    wait_for_rate_window()

    other_response = client.upload_bytes(other_name, f"notes {unique}".encode())
    assert other_response.status_code == 201, other_response.text
    wait_for_rate_window()

    response = client.list_files(params={"search": "incident-report"})

    assert response.status_code == 200, response.text
    payload = response.json()
    result_names = {item["original_filename"] for item in payload["results"]}
    assert payload["count"] == 1
    assert matching_name in result_names
    assert other_name not in result_names


def test_multi_page_pagination_returns_next_and_previous_links(client: FileVaultClient) -> None:
    unique = uuid4().hex
    filenames = [f"page-{index}-{unique}.txt" for index in range(3)]
    uploaded_ids: set[str] = set()

    for index, filename in enumerate(filenames):
        response = client.upload_bytes(filename, f"page payload {unique} {index}".encode())
        assert response.status_code == 201, response.text
        uploaded_ids.add(response.json()["id"])
        wait_for_rate_window()

    first_page = client.list_files(params={"page": 1, "page_size": 2})
    assert first_page.status_code == 200, first_page.text
    first_payload = first_page.json()
    assert first_payload["count"] == 3
    assert len(first_payload["results"]) == 2
    assert first_payload["next"] is not None
    assert first_payload["previous"] is None

    wait_for_rate_window()
    second_page = client.list_files(params={"page": 2, "page_size": 2})
    assert second_page.status_code == 200, second_page.text
    second_payload = second_page.json()
    assert second_payload["count"] == 3
    assert len(second_payload["results"]) == 1
    assert second_payload["next"] is None
    assert second_payload["previous"] is not None

    returned_ids = {
        item["id"]
        for payload in (first_payload, second_payload)
        for item in payload["results"]
    }
    assert returned_ids == uploaded_ids


def test_download_returns_original_plaintext_bytes(client: FileVaultClient) -> None:
    uploaded = upload_pdf(client)
    wait_for_rate_window()

    response = client.download_file(uploaded["id"])

    assert response.status_code == 200, response.text
    assert response.content == sample_pdf_path().read_bytes()
    assert "attachment" in response.headers.get("Content-Disposition", "")


def test_duplicate_upload_creates_reference_and_storage_savings(client: FileVaultClient) -> None:
    original = upload_pdf(client)
    wait_for_rate_window()
    duplicate_response = client.upload_file(sample_pdf_path(), content_type="application/pdf")
    assert duplicate_response.status_code == 201, duplicate_response.text
    duplicate = duplicate_response.json()
    assert_file_metadata_contract(duplicate)

    assert duplicate["id"] != original["id"]
    assert duplicate["file_hash"] == original["file_hash"]
    assert duplicate["is_reference"] is True
    assert duplicate["original_file"] == original["id"]

    wait_for_rate_window()
    stats_response = client.storage_stats()
    assert stats_response.status_code == 200, stats_response.text
    stats = stats_response.json()
    assert stats["user_id"] == client.user_id
    assert stats["storage_savings"] > 0
    assert stats["savings_percentage"] > 0


def test_delete_file_removes_access_to_deleted_record(client: FileVaultClient) -> None:
    uploaded = upload_pdf(client)
    wait_for_rate_window()

    delete_response = client.delete_file(uploaded["id"])
    assert delete_response.status_code == 204, delete_response.text

    wait_for_rate_window()
    retrieve_response = client.retrieve_file(uploaded["id"])
    assert retrieve_response.status_code == 404


def test_rate_limit_returns_429_on_third_rapid_request(client: FileVaultClient) -> None:
    first = client.list_files()
    second = client.list_files()
    third = client.list_files()

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 429
    assert third.json()["detail"] == "Call Limit Reached"


def test_rate_limit_is_consistent_for_concurrent_requests_across_workers() -> None:
    client = FileVaultClient(user_id=f"e2e-workers-{uuid4()}", timeout=10.0)

    def request_status() -> int:
        return client.list_files().status_code

    with ThreadPoolExecutor(max_workers=6) as executor:
        statuses = list(executor.map(lambda _: request_status(), range(6)))

    assert statuses.count(200) == 2
    assert statuses.count(429) == 4


def test_missing_user_id_returns_401() -> None:
    response = FileVaultClient().request_without_user_id()

    assert response.status_code == 401
    assert response.json()["detail"] == "UserId header required"


def test_quota_exceeded_returns_429_and_duplicate_bypasses_quota(client: FileVaultClient) -> None:
    uploaded_ids: list[str] = []
    first_payload = b"\x00" * (1024 * 1024)

    for index in range(10):
        payload = bytes([index]) * (1024 * 1024)
        response = client.upload_bytes(f"quota-{index}.bin", payload)
        assert response.status_code == 201, response.text
        uploaded_ids.append(response.json()["id"])
        wait_for_rate_window()

    over_quota = client.upload_bytes("quota-overflow.bin", b"\xff" * (1024 * 1024))
    assert over_quota.status_code == 429
    assert over_quota.json()["detail"] == "Storage Quota Exceeded"

    wait_for_rate_window()
    duplicate = client.upload_bytes("quota-duplicate.bin", first_payload)
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["is_reference"] is True

    wait_for_rate_window()
    delete_response = client.delete_file(uploaded_ids[0])
    assert delete_response.status_code == 204, delete_response.text


def test_file_types_endpoint_returns_user_scoped_mime_types(client: FileVaultClient) -> None:
    upload_pdf(client)
    wait_for_rate_window()

    response = client.file_types()

    assert response.status_code == 200, response.text
    assert response.json() == ["application/pdf"]
