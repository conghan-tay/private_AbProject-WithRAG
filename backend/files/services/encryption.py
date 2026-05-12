import base64
import binascii
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


AES_256_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12


class EncryptionService:
    """Encrypt and decrypt file bytes for storage at rest."""

    @classmethod
    def encrypt_file(cls, file_obj):
        key = cls._load_key()
        plaintext = cls._read_file(file_obj)
        iv = os.urandom(AES_GCM_NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(iv, plaintext, None)
        cls._reset_file_pointer(file_obj)
        return ciphertext, iv

    @classmethod
    def decrypt_file(cls, ciphertext, iv):
        key = cls._load_key()
        return AESGCM(key).decrypt(bytes(iv), ciphertext, None)

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

    @staticmethod
    def _read_file(file_obj):
        EncryptionService._reset_file_pointer(file_obj)
        if hasattr(file_obj, 'chunks'):
            return b''.join(file_obj.chunks())
        return file_obj.read()

    @staticmethod
    def _reset_file_pointer(file_obj):
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
