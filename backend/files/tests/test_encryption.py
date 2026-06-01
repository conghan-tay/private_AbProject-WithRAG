import io
import tempfile
from pathlib import Path

import pytest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.utils import setup_databases, teardown_databases
from django.urls import reverse
from rest_framework.test import APIClient

from files.models import File
from files.services.encryption import AES_GCM_NONCE_BYTES, AES_GCM_TAG_BYTES, EncryptionService


BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
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


def decrypt_temp_file(encrypted_file, iv, original_size):
    encrypted_file.seek(0)
    return b''.join(
        EncryptionService.decrypt_file_stream(
            encrypted_file,
            iv,
            original_size,
        )
    )


class ChunkOnlyUpload:
    def __init__(self, chunks):
        self._chunks = chunks
        self.chunk_size_calls = []
        self.seek_calls = []

    def chunks(self, chunk_size=None):
        self.chunk_size_calls.append(chunk_size)
        yield from self._chunks

    def read(self, *args, **kwargs):
        raise AssertionError('chunked encryption must not call read()')

    def seek(self, position):
        self.seek_calls.append(position)


class EncryptionServiceTests(TestCase):
    @override_settings(ENCRYPTION_CHUNK_SIZE_BYTES=5)
    def test_chunked_encrypt_decrypt_round_trips_multiple_chunks(self):
        plaintext_chunks = [b'abcde', b'fghij', b'kl']
        file_obj = ChunkOnlyUpload(plaintext_chunks)

        encrypted_file, iv = EncryptionService.encrypt_file_to_temp(file_obj)
        try:
            encrypted_bytes = encrypted_file.read()
            plaintext = b''.join(plaintext_chunks)

            assert encrypted_bytes != plaintext
            assert plaintext not in encrypted_bytes
            assert len(iv) == AES_GCM_NONCE_BYTES
            assert len(encrypted_bytes) == len(plaintext) + (3 * AES_GCM_TAG_BYTES)
            assert decrypt_temp_file(encrypted_file, iv, len(plaintext)) == plaintext
        finally:
            encrypted_file.close()

    def test_zero_byte_file_encrypts_to_non_empty_ciphertext(self):
        file_obj = SimpleUploadedFile('empty.txt', b'')

        encrypted_file, iv = EncryptionService.encrypt_file_to_temp(file_obj)
        try:
            encrypted_bytes = encrypted_file.read()

            assert encrypted_bytes != b''
            assert len(encrypted_bytes) == AES_GCM_TAG_BYTES
            assert decrypt_temp_file(encrypted_file, iv, 0) == b''
        finally:
            encrypted_file.close()

    @override_settings(ENCRYPTION_CHUNK_SIZE_BYTES=4)
    def test_chunked_encryption_consumes_chunks_instead_of_full_read(self):
        file_obj = ChunkOnlyUpload([b'abcd', b'ef'])

        encrypted_file, _ = EncryptionService.encrypt_file_to_temp(file_obj)
        encrypted_file.close()

        assert file_obj.chunk_size_calls == [4]
        assert file_obj.seek_calls == [0, 0]

    @override_settings(ENCRYPTION_CHUNK_SIZE_BYTES=5)
    def test_corrupted_encrypted_chunk_raises_decryption_error(self):
        file_obj = ChunkOnlyUpload([b'abcde', b'f'])
        encrypted_file, iv = EncryptionService.encrypt_file_to_temp(file_obj)
        try:
            encrypted_bytes = encrypted_file.read()
        finally:
            encrypted_file.close()

        corrupted = encrypted_bytes[:-1]

        with pytest.raises(Exception):
            b''.join(EncryptionService.decrypt_file_stream(io.BytesIO(corrupted), iv, 6))


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
