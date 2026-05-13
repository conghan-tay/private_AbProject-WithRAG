import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import django
from django.db.models import Sum
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.utils import setup_databases, teardown_databases
from django.urls import reverse
from rest_framework.test import APIClient

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from files.models import File
from files.services.dedup import DeduplicationService


FIXTURES_DIR = REPO_DIR / 'tests' / 'fixtures'
TEST_DATABASE_CONFIG = None


def setup_module():
    global TEST_DATABASE_CONFIG
    TEST_DATABASE_CONFIG = setup_databases(verbosity=0, interactive=False)


def teardown_module():
    if TEST_DATABASE_CONFIG is not None:
        teardown_databases(TEST_DATABASE_CONFIG, verbosity=0)


def response_body(response):
    return b''.join(response.streaming_content)


class DeduplicationServiceTests(TestCase):
    def test_compute_hash_returns_sha256_and_resets_file_pointer(self):
        content = b'file vault dedup bytes'
        file_obj = SimpleUploadedFile('sample.txt', content)

        digest = DeduplicationService.compute_hash(file_obj)

        assert digest == hashlib.sha256(content).hexdigest()
        assert file_obj.tell() == 0

    def test_find_duplicate_returns_same_user_original_only(self):
        file_hash = hashlib.sha256(b'same bytes').hexdigest()
        original = File.objects.create(
            user_id='owner',
            original_filename='original.txt',
            file_type='text/plain',
            size=10,
            file_hash=file_hash,
            is_reference=False,
            reference_count=1,
        )
        File.objects.create(
            user_id='owner',
            original_filename='reference.txt',
            file_type='text/plain',
            size=10,
            file_hash=file_hash,
            is_reference=True,
            original_file=original,
            reference_count=1,
        )
        File.objects.create(
            user_id='other',
            original_filename='other.txt',
            file_type='text/plain',
            size=10,
            file_hash=file_hash,
            is_reference=False,
            reference_count=1,
        )

        assert DeduplicationService.find_duplicate('owner', file_hash) == original
        assert DeduplicationService.find_duplicate('missing', file_hash) is None


class DeduplicatedUploadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.media_dir = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_dir.cleanup)

    def upload_pdf(self, user_id='dedup-user', filename='sample.pdf'):
        pdf_bytes = (FIXTURES_DIR / 'sample.pdf').read_bytes()
        uploaded_file = SimpleUploadedFile(
            filename,
            pdf_bytes,
            content_type='application/pdf',
        )
        return self.client.post(
            reverse('file-list'),
            {'file': uploaded_file},
            format='multipart',
            HTTP_USERID=user_id,
        )

    def test_duplicate_uploads_create_references_without_writing_new_files(self):
        original_response = self.upload_pdf(filename='original.pdf')
        duplicate_response = self.upload_pdf(filename='duplicate.pdf')
        third_response = self.upload_pdf(filename='third.pdf')

        assert original_response.status_code == 201
        assert duplicate_response.status_code == 201
        assert third_response.status_code == 201

        original_payload = original_response.json()
        duplicate_payload = duplicate_response.json()
        third_payload = third_response.json()

        assert original_payload['is_reference'] is False
        assert original_payload['reference_count'] == 1
        assert duplicate_payload['is_reference'] is True
        assert duplicate_payload['original_file'] == original_payload['id']
        assert duplicate_payload['file'] == original_payload['file']
        assert duplicate_payload['file_hash'] == original_payload['file_hash']
        assert duplicate_payload['size'] == original_payload['size']
        assert duplicate_payload['file_type'] == original_payload['file_type']
        assert duplicate_payload['original_filename'] == 'duplicate.pdf'
        assert third_payload['is_reference'] is True
        assert third_payload['original_file'] == original_payload['id']
        assert third_payload['file'] == original_payload['file']

        original = File.objects.get(id=original_payload['id'])
        duplicate = File.objects.get(id=duplicate_payload['id'])
        third = File.objects.get(id=third_payload['id'])

        assert original.reference_count == 3
        assert duplicate.file.name in (None, '')
        assert duplicate.encryption_iv is None
        assert third.file.name in (None, '')
        assert File.objects.filter(user_id='dedup-user', is_reference=False).count() == 1
        assert File.objects.filter(user_id='dedup-user', is_reference=True).count() == 2
        assert len(list(Path(self.media_dir.name).rglob('*.*'))) == 1

    def test_identical_upload_by_different_user_creates_separate_original(self):
        first_response = self.upload_pdf(user_id='user-one')
        second_response = self.upload_pdf(user_id='user-two')

        assert first_response.status_code == 201
        assert second_response.status_code == 201

        first_payload = first_response.json()
        second_payload = second_response.json()

        assert first_payload['file_hash'] == second_payload['file_hash']
        assert first_payload['is_reference'] is False
        assert second_payload['is_reference'] is False
        assert second_payload['original_file'] is None
        assert File.objects.filter(file_hash=first_payload['file_hash'], is_reference=False).count() == 2
        assert len(list(Path(self.media_dir.name).rglob('*.*'))) == 2

    def test_unique_constraint_race_creates_reference_and_cleans_orphaned_file(self):
        original_response = self.upload_pdf()
        assert original_response.status_code == 201
        original = File.objects.get(id=original_response.json()['id'])
        original_storage_name = original.file.name

        real_find_duplicate = DeduplicationService.find_duplicate
        call_count = {'count': 0}

        def miss_once_then_find(user_id, file_hash):
            call_count['count'] += 1
            if call_count['count'] == 1:
                return None
            return real_find_duplicate(user_id, file_hash)

        with patch.object(DeduplicationService, 'find_duplicate', side_effect=miss_once_then_find):
            race_response = self.upload_pdf(filename='raced.pdf')

        assert race_response.status_code == 201
        payload = race_response.json()
        original.refresh_from_db()

        assert payload['is_reference'] is True
        assert payload['original_file'] == str(original.id)
        assert original.reference_count == 2
        assert File.objects.filter(user_id='dedup-user', is_reference=False).count() == 1
        assert File.objects.filter(user_id='dedup-user', is_reference=True).count() == 1
        assert [path.name for path in Path(self.media_dir.name).rglob('*.*')] == [
            Path(original_storage_name).name,
        ]

    def test_reference_download_uses_original_bytes_and_reference_filename(self):
        plaintext = (FIXTURES_DIR / 'sample.pdf').read_bytes()
        self.upload_pdf(filename='original.pdf')
        duplicate_response = self.upload_pdf(filename='duplicate.pdf')
        duplicate_id = duplicate_response.json()['id']

        response = self.client.get(
            reverse('file-download', kwargs={'pk': duplicate_id}),
            HTTP_USERID='dedup-user',
        )

        assert response.status_code == 200
        assert response_body(response) == plaintext
        assert response['Content-Type'] == 'application/pdf'
        assert response['Content-Disposition'] == 'attachment; filename="duplicate.pdf"'

    def test_delete_reference_decrements_original_count_and_retains_physical_file(self):
        original_response = self.upload_pdf(filename='original.pdf')
        duplicate_response = self.upload_pdf(filename='duplicate.pdf')
        original = File.objects.get(id=original_response.json()['id'])
        duplicate_id = duplicate_response.json()['id']
        saved_path = Path(original.file.path)

        response = self.client.delete(
            reverse('file-detail', kwargs={'pk': duplicate_id}),
            HTTP_USERID='dedup-user',
        )

        assert response.status_code == 204
        original.refresh_from_db()
        assert original.reference_count == 1
        assert not File.objects.filter(id=duplicate_id).exists()
        assert saved_path.is_file()
        assert len(list(Path(self.media_dir.name).rglob('*.*'))) == 1

    def test_delete_original_with_references_promotes_oldest_reference(self):
        plaintext = (FIXTURES_DIR / 'sample.pdf').read_bytes()
        original_response = self.upload_pdf(filename='original.pdf')
        first_ref_response = self.upload_pdf(filename='first-reference.pdf')
        second_ref_response = self.upload_pdf(filename='second-reference.pdf')
        original_id = original_response.json()['id']
        first_ref_id = first_ref_response.json()['id']
        second_ref_id = second_ref_response.json()['id']

        original = File.objects.get(id=original_id)
        original_storage_name = original.file.name
        original_storage_path = Path(original.file.path)
        original_iv = original.encryption_iv

        response = self.client.delete(
            reverse('file-detail', kwargs={'pk': original_id}),
            HTTP_USERID='dedup-user',
        )

        assert response.status_code == 204
        assert not File.objects.filter(id=original_id).exists()

        promoted = File.objects.get(id=first_ref_id)
        remaining_reference = File.objects.get(id=second_ref_id)
        assert promoted.is_reference is False
        assert promoted.original_file is None
        assert promoted.reference_count == 2
        assert promoted.file.name == original_storage_name
        assert promoted.encryption_iv == original_iv
        assert promoted.original_filename == 'first-reference.pdf'
        assert remaining_reference.is_reference is True
        assert remaining_reference.original_file == promoted
        assert original_storage_path.is_file()
        assert len(list(Path(self.media_dir.name).rglob('*.*'))) == 1

        download_response = self.client.get(
            reverse('file-download', kwargs={'pk': promoted.id}),
            HTTP_USERID='dedup-user',
        )
        assert download_response.status_code == 200
        assert response_body(download_response) == plaintext
        assert download_response['Content-Disposition'] == (
            'attachment; filename="first-reference.pdf"'
        )

        retrieve_deleted_response = self.client.get(
            reverse('file-detail', kwargs={'pk': original_id}),
            HTTP_USERID='dedup-user',
        )
        assert retrieve_deleted_response.status_code == 404

    def test_delete_original_without_references_deletes_physical_file_and_frees_storage(self):
        original_response = self.upload_pdf(filename='original.pdf')
        original_id = original_response.json()['id']
        original = File.objects.get(id=original_id)
        saved_path = Path(original.file.path)

        response = self.client.delete(
            reverse('file-detail', kwargs={'pk': original_id}),
            HTTP_USERID='dedup-user',
        )

        assert response.status_code == 204
        assert not File.objects.filter(id=original_id).exists()
        assert not saved_path.exists()
        actual_storage_used = (
            File.objects.filter(user_id='dedup-user', is_reference=False).aggregate(
                total=Sum('size'),
            )['total']
            or 0
        )
        assert actual_storage_used == 0

    def test_delete_for_another_user_returns_404_and_preserves_file(self):
        original_response = self.upload_pdf(user_id='owner-user', filename='owner.pdf')
        original_id = original_response.json()['id']
        original = File.objects.get(id=original_id)
        saved_path = Path(original.file.path)

        response = self.client.delete(
            reverse('file-detail', kwargs={'pk': original_id}),
            HTTP_USERID='other-user',
        )

        assert response.status_code == 404
        assert File.objects.filter(id=original_id).exists()
        assert saved_path.is_file()
