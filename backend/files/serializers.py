from django.conf import settings
from rest_framework import serializers

from .models import File


class FileSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)

    class Meta:
        model = File
        fields = [
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
        ]
        read_only_fields = [
            'id',
            'user_id',
            'original_filename',
            'file_type',
            'size',
            'file_hash',
            'is_reference',
            'original_file',
            'reference_count',
            'uploaded_at',
        ]

    def validate_file(self, value):
        if value.size > settings.MAX_UPLOAD_SIZE_BYTES:
            raise serializers.ValidationError('File too large')
        return value
