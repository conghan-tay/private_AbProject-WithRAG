import uuid
import os

from django.db import models


def file_upload_path(instance, filename):
    """Generate file path for new file upload"""
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join('uploads', filename)


class File(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.CharField(max_length=128)
    file = models.FileField(upload_to=file_upload_path, null=True, blank=True)
    original_filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=128)
    size = models.BigIntegerField()
    file_hash = models.CharField(max_length=64)
    is_reference = models.BooleanField(default=False)
    original_file = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='references',
    )
    reference_count = models.PositiveIntegerField(default=1)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    encryption_iv = models.BinaryField(max_length=16, null=True, blank=True)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['user_id'], name='files_file_user_id'),
            models.Index(fields=['file_type'], name='files_file_file_type'),
            models.Index(fields=['size'], name='files_file_size'),
            models.Index(fields=['uploaded_at'], name='files_file_uploaded_at'),
            models.Index(fields=['file_hash'], name='files_file_file_hash'),
            models.Index(fields=['original_filename'], name='files_file_original_filename'),
            models.Index(fields=['user_id', 'uploaded_at'], name='files_file_user_uploaded_at'),
            models.Index(fields=['user_id', 'is_reference'], name='files_file_user_is_reference'),
        ]

    def __str__(self):
        return self.original_filename
