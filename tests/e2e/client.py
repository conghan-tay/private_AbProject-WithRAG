"""Requests-based E2E client for the Abnormal File Vault API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests


DEFAULT_BASE_URL = "http://localhost:8000"


class FileVaultClient:
    """Thin HTTP client used by the E2E progress dashboard."""

    def __init__(
        self,
        base_url: str | None = None,
        user_id: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("FILE_VAULT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.user_id = user_id or f"e2e-{uuid4()}"
        self.timeout = timeout

    @property
    def files_url(self) -> str:
        return f"{self.base_url}/api/files/"

    @property
    def headers(self) -> dict[str, str]:
        return {"UserId": self.user_id}

    def healthcheck(self) -> requests.Response:
        return requests.get(self.files_url, headers=self.headers, timeout=self.timeout)

    def list_files(self, params: dict[str, Any] | None = None) -> requests.Response:
        return requests.get(self.files_url, headers=self.headers, params=params, timeout=self.timeout)

    def retrieve_file(self, file_id: str) -> requests.Response:
        return requests.get(f"{self.files_url}{file_id}/", headers=self.headers, timeout=self.timeout)

    def upload_file(self, path: Path, content_type: str | None = None) -> requests.Response:
        with path.open("rb") as handle:
            files = {"file": (path.name, handle, content_type or "application/octet-stream")}
            return requests.post(self.files_url, headers=self.headers, files=files, timeout=self.timeout)

    def upload_bytes(
        self,
        filename: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> requests.Response:
        files = {"file": (filename, data, content_type)}
        return requests.post(self.files_url, headers=self.headers, files=files, timeout=self.timeout)

    def download_file(self, file_id: str) -> requests.Response:
        return requests.get(f"{self.files_url}{file_id}/download/", headers=self.headers, timeout=self.timeout)

    def delete_file(self, file_id: str) -> requests.Response:
        return requests.delete(f"{self.files_url}{file_id}/", headers=self.headers, timeout=self.timeout)

    def storage_stats(self) -> requests.Response:
        return requests.get(f"{self.files_url}storage_stats/", headers=self.headers, timeout=self.timeout)

    def file_types(self) -> requests.Response:
        return requests.get(f"{self.files_url}file_types/", headers=self.headers, timeout=self.timeout)

    def request_without_user_id(self) -> requests.Response:
        return requests.get(self.files_url, timeout=self.timeout)
