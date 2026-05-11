import hashlib

import magic
from rest_framework import viewsets, status
from rest_framework.response import Response

from .models import File
from .serializers import FileSerializer


HASH_CHUNK_SIZE = 8192
MIME_SAMPLE_SIZE = 2048
DEFAULT_MIME_TYPE = 'application/octet-stream'


def reset_file_pointer(file_obj):
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)


def compute_file_hash(file_obj):
    hasher = hashlib.sha256()
    reset_file_pointer(file_obj)
    for chunk in file_obj.chunks(chunk_size=HASH_CHUNK_SIZE):
        hasher.update(chunk)
    reset_file_pointer(file_obj)
    return hasher.hexdigest()


def detect_mime_type(file_obj):
    reset_file_pointer(file_obj)
    sample = file_obj.read(MIME_SAMPLE_SIZE)
    reset_file_pointer(file_obj)
    return magic.from_buffer(sample, mime=True) or DEFAULT_MIME_TYPE


class FileViewSet(viewsets.ModelViewSet):
    queryset = File.objects.all()
    serializer_class = FileSerializer

    def get_queryset(self):
        user_id = getattr(self.request, 'user_id', None)
        if not user_id:
            return File.objects.none()
        return File.objects.filter(user_id=user_id)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        file_obj = serializer.validated_data['file']
        serializer.save(
            user_id=request.user_id,
            original_filename=file_obj.name,
            file_type=detect_mime_type(file_obj),
            size=file_obj.size,
            file_hash=compute_file_hash(file_obj),
            is_reference=False,
            original_file=None,
            reference_count=1,
        )

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
