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
    @transaction.atomic
    def delete_reference(reference):
        reference = File.objects.select_related('original_file').select_for_update().get(
            pk=reference.pk,
        )
        original = reference.original_file
        if original is not None:
            File.objects.filter(pk=original.pk, reference_count__gt=1).update(
                reference_count=F('reference_count') - 1,
            )
        reference.delete()

    @staticmethod
    @transaction.atomic
    def promote_reference(original_file):
        original = File.objects.select_for_update().get(pk=original_file.pk)
        oldest_reference = (
            File.objects.select_for_update()
            .filter(original_file=original, is_reference=True)
            .order_by('uploaded_at', 'id')
            .first()
        )
        if oldest_reference is None:
            return None

        original_file_name = original.file.name
        original_encryption_iv = original.encryption_iv
        new_reference_count = max(original.reference_count - 1, 1)
        remaining_reference_ids = list(
            File.objects.filter(original_file=original, is_reference=True)
            .exclude(pk=oldest_reference.pk)
            .values_list('pk', flat=True)
        )

        original.delete()

        File.objects.filter(pk=oldest_reference.pk).update(
            file=original_file_name,
            is_reference=False,
            original_file=None,
            reference_count=new_reference_count,
            encryption_iv=original_encryption_iv,
        )
        promoted = File.objects.get(pk=oldest_reference.pk)

        if remaining_reference_ids:
            File.objects.filter(pk__in=remaining_reference_ids).update(original_file=promoted)

        return promoted

    @staticmethod
    @transaction.atomic
    def delete_original_file(original_file):
        original = File.objects.select_for_update().get(pk=original_file.pk)
        original.file.delete(save=False)
        original.delete()

    @staticmethod
    def _reset_file_pointer(file_obj):
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
