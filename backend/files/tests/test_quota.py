import hashlib
import tempfile
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.utils import setup_databases, teardown_databases
from django.urls import reverse
from rest_framework.test import APIClient

from files.models import File
from files.services.query import FileQueryService, QuotaExceededException


TEST_DATABASE_CONFIG = None


def setup_module():
    global TEST_DATABASE_CONFIG
    TEST_DATABASE_CONFIG = setup_databases(verbosity=0, interactive=False)


def teardown_module():
    if TEST_DATABASE_CONFIG is not None:
        teardown_databases(TEST_DATABASE_CONFIG, verbosity=0)


class FileQuotaServiceTests(TestCase):
    def create_file(
        self,
        user_id='quota-user',
        filename='stored.bin',
        size=5,
        file_hash=None,
        is_reference=False,
        original_file=None,
    ):
        hash_source = f'{user_id}:{filename}:{size}'.encode('utf-8')
        return File.objects.create(
            user_id=user_id,
            original_filename=filename,
            file_type='application/octet-stream',
            size=size,
            file_hash=file_hash or hashlib.sha256(hash_source).hexdigest(),
            is_reference=is_reference,
            original_file=original_file,
            reference_count=1,
        )

    @override_settings(STORAGE_LIMIT_BYTES=10)
    def test_check_quota_allows_upload_below_limit(self):
        self.create_file(size=4)

        assert FileQueryService.check_quota('quota-user', 5) is None

    @override_settings(STORAGE_LIMIT_BYTES=10)
    def test_check_quota_allows_exact_limit_boundary(self):
        self.create_file(size=6)

        assert FileQueryService.check_quota('quota-user', 4) is None

    @override_settings(STORAGE_LIMIT_BYTES=10)
    def test_check_quota_raises_when_new_original_exceeds_limit(self):
        self.create_file(size=6)

        with pytest.raises(QuotaExceededException) as exc:
            FileQueryService.check_quota('quota-user', 5)

        assert str(exc.value.detail) == 'Storage Quota Exceeded'
        assert exc.value.status_code == 429

    @override_settings(STORAGE_LIMIT_BYTES=10)
    def test_check_quota_excludes_references_from_actual_storage(self):
        original = self.create_file(size=9)
        self.create_file(
            filename='reference.bin',
            size=9,
            file_hash=original.file_hash,
            is_reference=True,
            original_file=original,
        )

        assert FileQueryService.check_quota('quota-user', 1) is None

    @override_settings(STORAGE_LIMIT_BYTES=10)
    def test_check_quota_is_scoped_per_user(self):
        self.create_file(user_id='other-user', size=10)

        assert FileQueryService.check_quota('quota-user', 10) is None


class FileQuotaUploadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.media_dir = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_dir.cleanup)

    def upload_bytes(self, filename, data, user_id='quota-user'):
        uploaded_file = SimpleUploadedFile(
            filename,
            data,
            content_type='application/octet-stream',
        )
        return self.client.post(
            reverse('file-list'),
            {'file': uploaded_file},
            format='multipart',
            HTTP_USERID=user_id,
        )

    @override_settings(STORAGE_LIMIT_BYTES=10)
    def test_upload_over_quota_returns_429_without_creating_record(self):
        response = self.upload_bytes('too-large.bin', b'x' * 11)

        assert response.status_code == 429
        assert response.json() == {'detail': 'Storage Quota Exceeded'}
        assert File.objects.filter(user_id='quota-user').count() == 0
        assert list(Path(self.media_dir.name).rglob('*.*')) == []

    @override_settings(STORAGE_LIMIT_BYTES=4)
    def test_duplicate_upload_bypasses_quota_and_creates_reference(self):
        original_response = self.upload_bytes('original.bin', b'abcd')
        duplicate_response = self.upload_bytes('duplicate.bin', b'abcd')

        assert original_response.status_code == 201
        assert duplicate_response.status_code == 201
        duplicate_payload = duplicate_response.json()
        original_payload = original_response.json()

        assert duplicate_payload['is_reference'] is True
        assert duplicate_payload['original_file'] == original_payload['id']
        assert File.objects.filter(user_id='quota-user', is_reference=False).count() == 1
        assert File.objects.filter(user_id='quota-user', is_reference=True).count() == 1
        assert len(list(Path(self.media_dir.name).rglob('*.*'))) == 1

    @override_settings(STORAGE_LIMIT_BYTES=5)
    def test_deleting_original_frees_quota_for_later_upload(self):
        original_response = self.upload_bytes('original.bin', b'abcde')
        blocked_response = self.upload_bytes('blocked.bin', b'z')

        assert original_response.status_code == 201
        assert blocked_response.status_code == 429
        assert blocked_response.json() == {'detail': 'Storage Quota Exceeded'}

        delete_response = self.client.delete(
            reverse('file-detail', kwargs={'pk': original_response.json()['id']}),
            HTTP_USERID='quota-user',
        )
        allowed_response = self.upload_bytes('allowed.bin', b'z')

        assert delete_response.status_code == 204
        assert allowed_response.status_code == 201
        assert allowed_response.json()['is_reference'] is False
