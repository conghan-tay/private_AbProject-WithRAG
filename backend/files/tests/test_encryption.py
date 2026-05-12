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
from files.services.encryption import AES_GCM_NONCE_BYTES, EncryptionService


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


class EncryptionServiceTests(TestCase):
    def test_encrypt_file_returns_ciphertext_and_iv(self):
        plaintext = b'file vault plaintext'
        file_obj = SimpleUploadedFile('sample.txt', plaintext)

        ciphertext, iv = EncryptionService.encrypt_file(file_obj)

        assert ciphertext != plaintext
        assert len(iv) == AES_GCM_NONCE_BYTES

    def test_decrypt_file_round_trips_to_plaintext(self):
        plaintext = b'round trip bytes'
        file_obj = SimpleUploadedFile('sample.txt', plaintext)

        ciphertext, iv = EncryptionService.encrypt_file(file_obj)

        assert EncryptionService.decrypt_file(ciphertext, iv) == plaintext


class EncryptedUploadDownloadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.media_dir = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_dir.cleanup)

    def upload_pdf(self, user_id='encryption-user'):
        pdf_path = FIXTURES_DIR / 'sample.pdf'
        uploaded_file = SimpleUploadedFile(
            'sample.pdf',
            pdf_path.read_bytes(),
            content_type='application/pdf',
        )
        return self.client.post(
            reverse('file-list'),
            {'file': uploaded_file},
            format='multipart',
            HTTP_USERID=user_id,
        )

    def test_upload_writes_ciphertext_and_stores_iv(self):
        plaintext = (FIXTURES_DIR / 'sample.pdf').read_bytes()

        response = self.upload_pdf()

        assert response.status_code == 201
        record = File.objects.get(id=response.json()['id'])
        assert record.encryption_iv is not None
        assert len(record.encryption_iv) == AES_GCM_NONCE_BYTES
        assert Path(record.file.path).read_bytes() != plaintext

    def test_download_returns_original_plaintext_and_attachment_header(self):
        plaintext = (FIXTURES_DIR / 'sample.pdf').read_bytes()
        upload_response = self.upload_pdf()
        file_id = upload_response.json()['id']

        response = self.client.get(
            reverse('file-download', kwargs={'pk': file_id}),
            HTTP_USERID='encryption-user',
        )

        assert response.status_code == 200
        assert response_body(response) == plaintext
        assert response['Content-Type'] == 'application/pdf'
        assert response['Content-Disposition'] == 'attachment; filename="sample.pdf"'

    def test_download_for_another_user_returns_404(self):
        upload_response = self.upload_pdf(user_id='owner-user')
        file_id = upload_response.json()['id']

        response = self.client.get(
            reverse('file-download', kwargs={'pk': file_id}),
            HTTP_USERID='other-user',
        )

        assert response.status_code == 404
