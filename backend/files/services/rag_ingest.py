from cryptography.exceptions import InvalidTag
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from files import rag_protocol as protocol
from files.models import File
from files.services.encryption import EncryptionService


class TxtIngestService:
    """Decrypt, decode, and chunk selected TXT vault files for a RAG session."""

    def ingest_files(self, user_id, file_ids, text_splitter=None):
        ordered_file_ids = self._deduplicate_file_ids(file_ids)
        if not ordered_file_ids:
            return {
                protocol.FIELD_INDEXED_FILES: 0,
                protocol.FIELD_SKIPPED_FILES: [],
                protocol.FIELD_CHUNKS: [],
            }

        records_by_id = {
            str(record.id): record
            for record in File.objects.select_related("original_file").filter(
                id__in=ordered_file_ids,
                user_id=user_id,
            )
        }
        splitter = text_splitter or self._default_text_splitter()

        indexed_files = 0
        skipped_files = []
        chunks = []

        for file_id in ordered_file_ids:
            record = records_by_id.get(file_id)
            if record is None:
                skipped_files.append(
                    {
                        protocol.FIELD_FILE_ID: file_id,
                        protocol.FIELD_REASON: protocol.SKIP_NOT_FOUND_OR_NOT_OWNED,
                    }
                )
                continue

            if record.file_type != protocol.SUPPORTED_TEXT_MIME_TYPE:
                skipped_files.append(
                    {
                        protocol.FIELD_FILE_ID: file_id,
                        protocol.FIELD_REASON: protocol.SKIP_UNSUPPORTED_TYPE,
                        protocol.FIELD_FILE_TYPE: record.file_type,
                    }
                )
                continue

            storage_record = record.original_file if record.is_reference else record
            if not self._has_usable_storage(storage_record):
                skipped_files.append(
                    {
                        protocol.FIELD_FILE_ID: file_id,
                        protocol.FIELD_REASON: protocol.SKIP_MALFORMED_STORAGE,
                    }
                )
                continue

            try:
                plaintext = self._decrypt_storage_record(storage_record)
            except (OSError, ValueError, InvalidTag):
                skipped_files.append(
                    {
                        protocol.FIELD_FILE_ID: file_id,
                        protocol.FIELD_REASON: protocol.SKIP_MALFORMED_STORAGE,
                    }
                )
                continue

            try:
                text = plaintext.decode("utf-8")
            except UnicodeDecodeError:
                skipped_files.append(
                    {
                        protocol.FIELD_FILE_ID: file_id,
                        protocol.FIELD_REASON: protocol.SKIP_UNSUPPORTED_ENCODING,
                    }
                )
                continue

            text_chunks = splitter.split_text(text)
            if not text_chunks:
                skipped_files.append(
                    {
                        protocol.FIELD_FILE_ID: file_id,
                        protocol.FIELD_REASON: protocol.SKIP_NO_CHUNKS,
                    }
                )
                continue

            indexed_files += 1
            for chunk_index, page_content in enumerate(text_chunks):
                chunks.append(
                    {
                        protocol.FIELD_PAGE_CONTENT: page_content,
                        protocol.FIELD_METADATA: {
                            protocol.FIELD_USER_ID: user_id,
                            protocol.FIELD_FILE_ID: file_id,
                            protocol.FIELD_STORAGE_FILE_ID: str(storage_record.id),
                            protocol.FIELD_ORIGINAL_FILENAME: record.original_filename,
                            protocol.FIELD_FILE_TYPE: record.file_type,
                            protocol.FIELD_CHUNK_INDEX: chunk_index,
                        },
                    }
                )

        return {
            protocol.FIELD_INDEXED_FILES: indexed_files,
            protocol.FIELD_SKIPPED_FILES: skipped_files,
            protocol.FIELD_CHUNKS: chunks,
        }

    @staticmethod
    def _deduplicate_file_ids(file_ids):
        seen = set()
        ordered_file_ids = []
        for file_id in file_ids:
            file_id = str(file_id)
            if file_id in seen:
                continue
            seen.add(file_id)
            ordered_file_ids.append(file_id)
        return ordered_file_ids

    @staticmethod
    def _has_usable_storage(storage_record):
        return (
            storage_record is not None
            and bool(storage_record.file)
            and storage_record.encryption_iv is not None
        )

    @staticmethod
    def _decrypt_storage_record(storage_record):
        storage_record.file.open("rb")
        try:
            return b"".join(
                EncryptionService.decrypt_file_stream(
                    storage_record.file,
                    storage_record.encryption_iv,
                    storage_record.size,
                )
            )
        finally:
            storage_record.file.close()

    @staticmethod
    def _default_text_splitter():
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
        except ImportError as exc:
            raise ImproperlyConfigured(
                "langchain-text-splitters is required for default TXT chunking"
            ) from exc

        return RecursiveCharacterTextSplitter(
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
        )
