from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from files.models import File
from files.services.encryption import EncryptionService


class TxtIngestService:
    """Decrypt, decode, and chunk selected TXT vault files for a RAG session."""

    def ingest_files(self, user_id, file_ids, text_splitter=None):
        ordered_file_ids = self._deduplicate_file_ids(file_ids)
        if not ordered_file_ids:
            return {"indexed_files": 0, "skipped_files": [], "chunks": []}

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
                    {"file_id": file_id, "reason": "not_found_or_not_owned"}
                )
                continue

            if record.file_type != "text/plain":
                skipped_files.append(
                    {
                        "file_id": file_id,
                        "reason": "unsupported_type",
                        "file_type": record.file_type,
                    }
                )
                continue

            storage_record = record.original_file if record.is_reference else record
            if not self._has_usable_storage(storage_record):
                skipped_files.append(
                    {"file_id": file_id, "reason": "malformed_storage"}
                )
                continue

            try:
                plaintext = self._decrypt_storage_record(storage_record)
            except (OSError, ValueError):
                skipped_files.append(
                    {"file_id": file_id, "reason": "malformed_storage"}
                )
                continue

            try:
                text = plaintext.decode("utf-8")
            except UnicodeDecodeError:
                skipped_files.append(
                    {"file_id": file_id, "reason": "unsupported_encoding"}
                )
                continue

            text_chunks = splitter.split_text(text)
            if not text_chunks:
                skipped_files.append({"file_id": file_id, "reason": "no_chunks"})
                continue

            indexed_files += 1
            for chunk_index, page_content in enumerate(text_chunks):
                chunks.append(
                    {
                        "page_content": page_content,
                        "metadata": {
                            "user_id": user_id,
                            "file_id": file_id,
                            "storage_file_id": str(storage_record.id),
                            "original_filename": record.original_filename,
                            "file_type": record.file_type,
                            "chunk_index": chunk_index,
                        },
                    }
                )

        return {
            "indexed_files": indexed_files,
            "skipped_files": skipped_files,
            "chunks": chunks,
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
