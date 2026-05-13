from django.conf import settings
from rest_framework import serializers

from .models import File


class FileSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, allow_empty_file=True)

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

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['file'] = self.get_file_url(instance)
        return representation

    @staticmethod
    def get_file_url(instance):
        storage_record = instance.original_file if instance.is_reference else instance
        if storage_record is None or not storage_record.file:
            return None

        try:
            return storage_record.file.url
        except ValueError:
            return None
