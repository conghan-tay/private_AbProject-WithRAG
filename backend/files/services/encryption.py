import base64
import binascii
import hashlib
import os
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


AES_256_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12
AES_GCM_TAG_BYTES = 16
CHUNK_NONCE_PREFIX_BYTES = 4
CHUNK_NONCE_COUNTER_BYTES = 8
CHUNK_INDEX_AAD_BYTES = 8


class EncryptionService:
    """Encrypt and decrypt file bytes for storage at rest."""

    @classmethod
    def encrypt_file_to_temp(cls, file_obj):
        key = cls._load_key()
        base_nonce = os.urandom(AES_GCM_NONCE_BYTES)
        aesgcm = AESGCM(key)
        encrypted_file = tempfile.TemporaryFile()

        try:
            wrote_chunk = False
            cls._reset_file_pointer(file_obj)
            for chunk_index, plaintext_chunk in enumerate(cls._iter_chunks(file_obj)):
                encrypted_file.write(
                    aesgcm.encrypt(
                        cls._derive_chunk_nonce(base_nonce, chunk_index),
                        plaintext_chunk,
                        cls._chunk_aad(chunk_index),
                    )
                )
                wrote_chunk = True

            if not wrote_chunk:
                encrypted_file.write(
                    aesgcm.encrypt(
                        cls._derive_chunk_nonce(base_nonce, 0),
                        b'',
                        cls._chunk_aad(0),
                    )
                )
        except Exception:
            encrypted_file.close()
            raise

        cls._reset_file_pointer(file_obj)
        encrypted_file.seek(0)
        return encrypted_file, base_nonce

    @classmethod
    def decrypt_file_stream(cls, encrypted_file_obj, iv, original_size):
        key = cls._load_key()
        aesgcm = AESGCM(key)
        base_nonce = bytes(iv)
        chunk_size = cls._chunk_size()

        if original_size == 0:
            encrypted_chunk = encrypted_file_obj.read(AES_GCM_TAG_BYTES)
            if len(encrypted_chunk) != AES_GCM_TAG_BYTES:
                raise ValueError('Encrypted file ended before zero-byte chunk')
            yield aesgcm.decrypt(
                cls._derive_chunk_nonce(base_nonce, 0),
                encrypted_chunk,
                cls._chunk_aad(0),
            )
            cls._ensure_no_trailing_bytes(encrypted_file_obj)
            return

        remaining = original_size
        chunk_index = 0
        while remaining > 0:
            plaintext_size = min(chunk_size, remaining)
            encrypted_size = plaintext_size + AES_GCM_TAG_BYTES
            encrypted_chunk = encrypted_file_obj.read(encrypted_size)
            if len(encrypted_chunk) != encrypted_size:
                raise ValueError('Encrypted file ended before expected chunk boundary')

            yield aesgcm.decrypt(
                cls._derive_chunk_nonce(base_nonce, chunk_index),
                encrypted_chunk,
                cls._chunk_aad(chunk_index),
            )
            remaining -= plaintext_size
            chunk_index += 1

        cls._ensure_no_trailing_bytes(encrypted_file_obj)

    @classmethod
    def _load_key(cls):
        configured_key = getattr(settings, 'ENCRYPTION_KEY', None) or os.environ.get('ENCRYPTION_KEY')
        if configured_key:
            return cls._decode_configured_key(configured_key)

        if settings.DEBUG:
            return hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()

        raise ImproperlyConfigured('ENCRYPTION_KEY must be set when DEBUG=False')

    @staticmethod
    def _decode_configured_key(configured_key):
        try:
            key = base64.urlsafe_b64decode(configured_key)
        except (TypeError, ValueError, binascii.Error) as exc:
            raise ImproperlyConfigured('ENCRYPTION_KEY must be base64-encoded') from exc

        if len(key) != AES_256_KEY_BYTES:
            raise ImproperlyConfigured('ENCRYPTION_KEY must decode to 32 bytes')
        return key

    @classmethod
    def _iter_chunks(cls, file_obj):
        chunk_size = cls._chunk_size()
        if hasattr(file_obj, 'chunks'):
            yield from file_obj.chunks(chunk_size=chunk_size)
            return

        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                return
            yield chunk

    @staticmethod
    def _derive_chunk_nonce(base_nonce, chunk_index):
        if len(base_nonce) != AES_GCM_NONCE_BYTES:
            raise ValueError('AES-GCM nonce must be 12 bytes')

        prefix = base_nonce[:CHUNK_NONCE_PREFIX_BYTES]
        base_counter = int.from_bytes(base_nonce[CHUNK_NONCE_PREFIX_BYTES:], 'big')
        max_counter = 1 << (CHUNK_NONCE_COUNTER_BYTES * 8)
        if base_counter + chunk_index >= max_counter:
            raise ValueError('Chunk nonce counter exhausted')

        counter = base_counter + chunk_index
        return prefix + counter.to_bytes(CHUNK_NONCE_COUNTER_BYTES, 'big')

    @staticmethod
    def _chunk_aad(chunk_index):
        return chunk_index.to_bytes(CHUNK_INDEX_AAD_BYTES, 'big')

    @staticmethod
    def _ensure_no_trailing_bytes(encrypted_file_obj):
        if encrypted_file_obj.read(1):
            raise ValueError('Encrypted file has trailing bytes')

    @staticmethod
    def _chunk_size():
        chunk_size = settings.ENCRYPTION_CHUNK_SIZE_BYTES
        if chunk_size <= 0:
            raise ImproperlyConfigured('ENCRYPTION_CHUNK_SIZE_BYTES must be positive')
        return chunk_size

    @staticmethod
    def _reset_file_pointer(file_obj):
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
