import hashlib
import os
import sys
import tempfile
from pathlib import Path

import django

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.utils import setup_databases, teardown_databases
from django.urls import reverse
from rest_framework.test import APIClient

from files.models import File


TEST_DATABASE_CONFIG = None


def setup_module():
    global TEST_DATABASE_CONFIG
    TEST_DATABASE_CONFIG = setup_databases(verbosity=0, interactive=False)


def teardown_module():
    if TEST_DATABASE_CONFIG is not None:
        teardown_databases(TEST_DATABASE_CONFIG, verbosity=0)


def response_body(response):
    return b''.join(response.streaming_content)


class FileEdgeCaseTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.media_dir = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_dir.cleanup)

    def upload_bytes(self, filename, data, user_id='edge-user', content_type='text/plain'):
        uploaded_file = SimpleUploadedFile(filename, data, content_type=content_type)
        return self.client.post(
            reverse('file-list'),
            {'file': uploaded_file},
            format='multipart',
            HTTP_USERID=user_id,
        )

    def test_zero_byte_file_upload_download_and_deduplication(self):
        first_response = self.upload_bytes('empty.txt', b'')
        second_response = self.upload_bytes('empty-copy.txt', b'')

        assert first_response.status_code == 201
        assert second_response.status_code == 201

        original_payload = first_response.json()
        reference_payload = second_response.json()
        original = File.objects.get(id=original_payload['id'])

        assert original_payload['size'] == 0
        assert original_payload['file_hash'] == hashlib.sha256(b'').hexdigest()
        assert original_payload['is_reference'] is False
        assert reference_payload['is_reference'] is True
        assert reference_payload['original_file'] == original_payload['id']
        assert reference_payload['file_hash'] == original_payload['file_hash']

        original.refresh_from_db()
        assert original.reference_count == 2
        assert Path(original.file.path).read_bytes() != b''
        assert len(list(Path(self.media_dir.name).rglob('*.*'))) == 1

        download_response = self.client.get(
            reverse('file-download', kwargs={'pk': original_payload['id']}),
            HTTP_USERID='edge-user',
        )

        assert download_response.status_code == 200
        assert response_body(download_response) == b''

    def test_mime_detection_uses_content_not_extension_or_request_header(self):
        jpeg_bytes = (
            b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H'
            b'\x00\x00\xff\xdb\x00C\x00'
            + (b'\x08' * 64)
            + b'\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03\x01\x11\x00'
            b'\x02\x11\x01\x03\x11\x01\xff\xd9'
        )

        response = self.upload_bytes(
            'spoof.txt',
            jpeg_bytes,
            content_type='text/plain',
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload['original_filename'] == 'spoof.txt'
        assert payload['file_type'] != 'text/plain'
        assert payload['file_type'].startswith('image/')

    def test_sql_injection_like_search_does_not_broaden_results(self):
        owned_match = self.upload_bytes('literal-match.txt', b'owned search target')
        owned_other = self.upload_bytes('regular.txt', b'owned other')
        other_user = self.upload_bytes(
            'literal-match.txt',
            b'other user target',
            user_id='other-user',
        )

        assert owned_match.status_code == 201
        assert owned_other.status_code == 201
        assert other_user.status_code == 201

        injection_response = self.client.get(
            reverse('file-list'),
            {'search': "' OR 1=1 --"},
            HTTP_USERID='edge-user',
        )
        exact_response = self.client.get(
            reverse('file-list'),
            {'search': 'literal-match'},
            HTTP_USERID='edge-user',
        )

        assert injection_response.status_code == 200
        injection_payload = injection_response.json()
        assert injection_payload['count'] == 0
        assert injection_payload['results'] == []

        assert exact_response.status_code == 200
        exact_payload = exact_response.json()
        assert exact_payload['count'] == 1
        assert exact_payload['results'][0]['id'] == owned_match.json()['id']
        assert exact_payload['results'][0]['user_id'] == 'edge-user'
