import hashlib

from django.db import transaction
from django.db.models import F

from files.models import File


HASH_CHUNK_SIZE = 8192


class DeduplicationService:
    """Hash uploaded files and create per-user deduplication references."""

    @classmethod
    def compute_hash(cls, file_obj):
        hasher = hashlib.sha256()
        cls._reset_file_pointer(file_obj)
        for chunk in file_obj.chunks(chunk_size=HASH_CHUNK_SIZE):
            hasher.update(chunk)
        cls._reset_file_pointer(file_obj)
        return hasher.hexdigest()

    @staticmethod
    def find_duplicate(user_id, file_hash):
        return File.objects.filter(
            user_id=user_id,
            file_hash=file_hash,
            is_reference=False,
        ).first()

    @staticmethod
    @transaction.atomic
    def create_reference(user_id, original, filename):
        reference = File.objects.create(
            user_id=user_id,
            file=None,
            original_filename=filename,
            file_type=original.file_type,
            size=original.size,
            file_hash=original.file_hash,
            is_reference=True,
            original_file=original,
            reference_count=1,
            encryption_iv=None,
        )
        File.objects.filter(pk=original.pk).update(reference_count=F('reference_count') + 1)
        original.refresh_from_db(fields=['reference_count'])
        return reference

    @staticmethod
    def _reset_file_pointer(file_obj):
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
