from django.core.files.base import ContentFile
from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import StreamingHttpResponse
from django.utils.http import content_disposition_header
import magic
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .models import File
from .serializers import FileSerializer
from .services.dedup import DeduplicationService
from .services.encryption import EncryptionService
from .services.query import FileQueryService


MIME_SAMPLE_SIZE = 2048
DEFAULT_MIME_TYPE = 'application/octet-stream'


class FilePagination(PageNumberPagination):
    page_size = settings.DEFAULT_PAGE_SIZE
    page_size_query_param = 'page_size'
    max_page_size = settings.MAX_PAGE_SIZE


def reset_file_pointer(file_obj):
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)


def detect_mime_type(file_obj):
    reset_file_pointer(file_obj)
    sample = file_obj.read(MIME_SAMPLE_SIZE)
    reset_file_pointer(file_obj)
    return magic.from_buffer(sample, mime=True) or DEFAULT_MIME_TYPE


class FileViewSet(viewsets.ModelViewSet):
    queryset = File.objects.all()
    serializer_class = FileSerializer
    pagination_class = FilePagination

    def get_queryset(self):
        user_id = getattr(self.request, 'user_id', None)
        if not user_id:
            return File.objects.none()
        return File.objects.filter(user_id=user_id)

    def list(self, request, *args, **kwargs):
        queryset = FileQueryService.build_queryset(request.user_id, request.query_params)
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        file_obj = serializer.validated_data['file']
        original_filename = file_obj.name
        file_type = detect_mime_type(file_obj)
        file_hash = DeduplicationService.compute_hash(file_obj)

        duplicate = DeduplicationService.find_duplicate(request.user_id, file_hash)
        if duplicate:
            reference = DeduplicationService.create_reference(
                request.user_id,
                duplicate,
                original_filename,
            )
            output_serializer = self.get_serializer(reference)
            return Response(output_serializer.data, status=status.HTTP_201_CREATED)

        ciphertext, iv = EncryptionService.encrypt_file(file_obj)
        record = File(
            user_id=request.user_id,
            original_filename=original_filename,
            file_type=file_type,
            size=file_obj.size,
            file_hash=file_hash,
            is_reference=False,
            original_file=None,
            reference_count=1,
            encryption_iv=iv,
        )
        record.file.save(original_filename, ContentFile(ciphertext), save=False)

        try:
            with transaction.atomic():
                record.save(force_insert=True)
        except IntegrityError:
            record.file.delete(save=False)
            duplicate = DeduplicationService.find_duplicate(request.user_id, file_hash)
            if not duplicate:
                raise
            reference = DeduplicationService.create_reference(
                request.user_id,
                duplicate,
                original_filename,
            )
            output_serializer = self.get_serializer(reference)
            return Response(output_serializer.data, status=status.HTTP_201_CREATED)

        output_serializer = self.get_serializer(record)
        headers = self.get_success_headers(output_serializer.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def destroy(self, request, *args, **kwargs):
        record = self.get_object()
        if record.is_reference:
            DeduplicationService.delete_reference(record)
        elif record.reference_count > 1:
            DeduplicationService.promote_reference(record)
        else:
            DeduplicationService.delete_original_file(record)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'])
    def storage_stats(self, request, *args, **kwargs):
        return Response(FileQueryService.get_storage_stats(request.user_id))

    @action(detail=False, methods=['get'])
    def file_types(self, request, *args, **kwargs):
        return Response(FileQueryService.get_file_types(request.user_id))

    @action(detail=True, methods=['get'])
    def download(self, request, *args, **kwargs):
        record = self.get_object()
        storage_record = record.original_file if record.is_reference else record
        if storage_record is None or not storage_record.file:
            raise NotFound()

        storage_record.file.open('rb')
        try:
            plaintext = EncryptionService.decrypt_file(
                storage_record.file.read(),
                storage_record.encryption_iv,
            )
        finally:
            storage_record.file.close()

        response = StreamingHttpResponse(
            iter([plaintext]),
            content_type=record.file_type,
        )
        response['Content-Disposition'] = content_disposition_header(
            as_attachment=True,
            filename=record.original_filename,
        )
        return response
