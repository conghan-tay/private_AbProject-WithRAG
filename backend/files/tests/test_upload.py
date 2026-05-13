import hashlib
import os
import sys
import tempfile
from pathlib import Path

import django
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


FIXTURES_DIR = REPO_DIR / 'tests' / 'fixtures'
TEST_DATABASE_CONFIG = None


def setup_module():
    global TEST_DATABASE_CONFIG
    TEST_DATABASE_CONFIG = setup_databases(verbosity=0, interactive=False)


def teardown_module():
    if TEST_DATABASE_CONFIG is not None:
        teardown_databases(TEST_DATABASE_CONFIG, verbosity=0)


class FileUploadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.media_dir = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_dir.cleanup)

    def upload_pdf(self, content_type='text/plain'):
        pdf_path = FIXTURES_DIR / 'sample.pdf'
        uploaded_file = SimpleUploadedFile(
            'sample.pdf',
            pdf_path.read_bytes(),
            content_type=content_type,
        )
        return self.client.post(
            reverse('file-list'),
            {'file': uploaded_file},
            format='multipart',
            HTTP_USERID='upload-user',
        )

    def test_upload_pdf_returns_step_5_metadata_contract(self):
        pdf_bytes = (FIXTURES_DIR / 'sample.pdf').read_bytes()

        response = self.upload_pdf()

        assert response.status_code == 201
        payload = response.json()
        assert set(payload.keys()) == {
            'id',
            'file',
            'user_id',
            'original_filename',
            'file_type',
            'size',
            'file_hash',
            'is_reference',
            'original_file',
            'reference_count',
            'uploaded_at',
        }
        assert payload['user_id'] == 'upload-user'
        assert payload['original_filename'] == 'sample.pdf'
        assert payload['file'].startswith('/media/uploads/')
        assert payload['file_type'] == 'application/pdf'
        assert payload['size'] == len(pdf_bytes)
        assert payload['file_hash'] == hashlib.sha256(pdf_bytes).hexdigest()
        assert len(payload['file_hash']) == 64
        assert payload['is_reference'] is False
        assert payload['original_file'] is None
        assert payload['reference_count'] == 1

    def test_upload_creates_database_record_and_writes_ciphertext_to_temp_media(self):
        response = self.upload_pdf()

        assert response.status_code == 201
        record = File.objects.get(id=response.json()['id'])
        saved_path = Path(record.file.path)
        plaintext = (FIXTURES_DIR / 'sample.pdf').read_bytes()

        assert record.user_id == 'upload-user'
        assert record.file_type == 'application/pdf'
        assert saved_path.is_file()
        assert saved_path.is_relative_to(Path(self.media_dir.name))
        assert saved_path.read_bytes() != plaintext

    def test_upload_list_and_detail_responses_include_file_url(self):
        upload_response = self.upload_pdf()
        assert upload_response.status_code == 201
        upload_payload = upload_response.json()

        list_response = self.client.get(reverse('file-list'), HTTP_USERID='upload-user')
        detail_response = self.client.get(
            reverse('file-detail', kwargs={'pk': upload_payload['id']}),
            HTTP_USERID='upload-user',
        )

        assert list_response.status_code == 200
        list_payload = list_response.json()
        assert list_payload['results'][0]['file'] == upload_payload['file']

        assert detail_response.status_code == 200
        assert detail_response.json()['file'] == upload_payload['file']
