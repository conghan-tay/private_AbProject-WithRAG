import hashlib
import tempfile
from uuid import uuid4

from django.core.files.base import File as DjangoFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.utils import setup_databases, teardown_databases

from files.models import File
from files.services.encryption import EncryptionService


TEST_DATABASE_CONFIG = None


def setup_module():
    global TEST_DATABASE_CONFIG
    TEST_DATABASE_CONFIG = setup_databases(verbosity=0, interactive=False)


def teardown_module():
    if TEST_DATABASE_CONFIG is not None:
        teardown_databases(TEST_DATABASE_CONFIG, verbosity=0)


def sorted_skips(skipped_files):
    return sorted(skipped_files, key=lambda item: (item["file_id"], item["reason"]))


class SplitTextOnlySplitter:
    """Contract double: TxtIngestService should call split_text(text)."""

    def __init__(self):
        self.seen_texts = []

    def split_text(self, text):
        self.seen_texts.append(text)
        return [chunk for chunk in text.split("\n\n") if chunk]


class TxtIngestServiceTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_dir.cleanup)

    def service(self):
        from files.services.rag_ingest import TxtIngestService

        return TxtIngestService()

    def create_encrypted_record(
        self,
        *,
        user_id="rag-user",
        filename="notes.txt",
        file_type="text/plain",
        plaintext=b"first chunk\n\nsecond chunk",
        is_reference=False,
        original_file=None,
        file_hash=None,
    ):
        if is_reference:
            return File.objects.create(
                user_id=user_id,
                file=None,
                original_filename=filename,
                file_type=file_type,
                size=original_file.size,
                file_hash=file_hash or original_file.file_hash,
                is_reference=True,
                original_file=original_file,
                reference_count=1,
                encryption_iv=None,
            )

        upload = SimpleUploadedFile(filename, plaintext, content_type=file_type)
        encrypted_file, iv = EncryptionService.encrypt_file_to_temp(upload)
        record = File(
            user_id=user_id,
            original_filename=filename,
            file_type=file_type,
            size=len(plaintext),
            file_hash=file_hash or hashlib.sha256(plaintext).hexdigest(),
            is_reference=False,
            original_file=None,
            reference_count=1,
            encryption_iv=iv,
        )
        try:
            record.file.save(filename, DjangoFile(encrypted_file), save=False)
        finally:
            encrypted_file.close()
        record.save(force_insert=True)
        return record

    def test_owned_txt_file_is_decrypted_decoded_split_and_returned_with_metadata(self):
        record = self.create_encrypted_record(
            plaintext=b"alpha facts\n\nbeta facts",
            filename="case-notes.txt",
        )
        splitter = SplitTextOnlySplitter()

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(record.id)],
            text_splitter=splitter,
        )

        assert result["indexed_files"] == 1
        assert result["skipped_files"] == []
        assert splitter.seen_texts == ["alpha facts\n\nbeta facts"]
        assert [chunk["page_content"] for chunk in result["chunks"]] == [
            "alpha facts",
            "beta facts",
        ]
        assert result["chunks"][0]["metadata"] == {
            "user_id": "rag-user",
            "file_id": str(record.id),
            "storage_file_id": str(record.id),
            "original_filename": "case-notes.txt",
            "file_type": "text/plain",
            "chunk_index": 0,
        }
        assert result["chunks"][1]["metadata"]["chunk_index"] == 1

    @override_settings(RAG_CHUNK_SIZE=40, RAG_CHUNK_OVERLAP=10)
    def test_default_langchain_splitter_uses_rag_chunk_settings(self):
        text = (
            "alpha bravo charlie delta echo foxtrot golf hotel india juliet "
            "kilo lima mike november oscar papa quebec romeo sierra tango"
        )
        record = self.create_encrypted_record(
            plaintext=text.encode("utf-8"),
            filename="long-notes.txt",
        )

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(record.id)],
            text_splitter=None,
        )

        assert result["indexed_files"] == 1
        assert result["skipped_files"] == []
        assert len(result["chunks"]) > 1

        for chunk_index, chunk in enumerate(result["chunks"]):
            assert chunk["page_content"]
            assert len(chunk["page_content"]) <= 40
            assert chunk["metadata"] == {
                "user_id": "rag-user",
                "file_id": str(record.id),
                "storage_file_id": str(record.id),
                "original_filename": "long-notes.txt",
                "file_type": "text/plain",
                "chunk_index": chunk_index,
            }

    def test_missing_and_cross_user_file_ids_are_skipped_without_existence_leak(self):
        other_record = self.create_encrypted_record(user_id="other-user")
        missing_id = uuid4()
        splitter = SplitTextOnlySplitter()

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(missing_id), str(other_record.id)],
            text_splitter=splitter,
        )

        assert result["indexed_files"] == 0
        assert result["chunks"] == []
        expected_skips = [
            {"file_id": str(missing_id), "reason": "not_found_or_not_owned"},
            {"file_id": str(other_record.id), "reason": "not_found_or_not_owned"},
        ]
        assert sorted_skips(result["skipped_files"]) == sorted_skips(expected_skips)
        assert splitter.seen_texts == []

    def test_owned_non_txt_file_is_skipped_as_unsupported_type(self):
        record = self.create_encrypted_record(
            filename="receipt.pdf",
            file_type="application/pdf",
            plaintext=b"%PDF-1.4 bytes",
        )

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(record.id)],
            text_splitter=SplitTextOnlySplitter(),
        )

        assert result["indexed_files"] == 0
        assert result["chunks"] == []
        assert result["skipped_files"] == [
            {
                "file_id": str(record.id),
                "reason": "unsupported_type",
                "file_type": "application/pdf",
            }
        ]

    def test_invalid_utf8_txt_is_skipped_as_unsupported_encoding(self):
        record = self.create_encrypted_record(
            filename="bad.txt",
            file_type="text/plain",
            plaintext=b"valid prefix \xff invalid utf8",
        )

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(record.id)],
            text_splitter=SplitTextOnlySplitter(),
        )

        assert result["indexed_files"] == 0
        assert result["chunks"] == []
        assert result["skipped_files"] == [
            {"file_id": str(record.id), "reason": "unsupported_encoding"}
        ]

    def test_reference_uses_original_storage_but_selected_reference_id_in_metadata(self):
        original = self.create_encrypted_record(
            plaintext=b"reference backed text",
            filename="original.txt",
        )
        reference = self.create_encrypted_record(
            filename="duplicate-name.txt",
            is_reference=True,
            original_file=original,
        )

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(reference.id)],
            text_splitter=SplitTextOnlySplitter(),
        )

        assert result["indexed_files"] == 1
        assert result["skipped_files"] == []
        assert result["chunks"] == [
            {
                "page_content": "reference backed text",
                "metadata": {
                    "user_id": "rag-user",
                    "file_id": str(reference.id),
                    "storage_file_id": str(original.id),
                    "original_filename": "duplicate-name.txt",
                    "file_type": "text/plain",
                    "chunk_index": 0,
                },
            }
        ]

    def test_malformed_reference_storage_is_skipped(self):
        malformed = File.objects.create(
            user_id="rag-user",
            file=None,
            original_filename="orphan-reference.txt",
            file_type="text/plain",
            size=10,
            file_hash=hashlib.sha256(b"orphan").hexdigest(),
            is_reference=True,
            original_file=None,
            reference_count=1,
            encryption_iv=None,
        )

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(malformed.id)],
            text_splitter=SplitTextOnlySplitter(),
        )

        assert result["indexed_files"] == 0
        assert result["chunks"] == []
        assert result["skipped_files"] == [
            {"file_id": str(malformed.id), "reason": "malformed_storage"}
        ]

    def test_owned_original_with_missing_storage_file_is_skipped_as_malformed_storage(self):
        malformed = File.objects.create(
            user_id="rag-user",
            file=None,
            original_filename="missing-storage.txt",
            file_type="text/plain",
            size=10,
            file_hash=hashlib.sha256(b"missing-storage").hexdigest(),
            is_reference=False,
            original_file=None,
            reference_count=1,
            encryption_iv=b"\x00" * 12,
        )

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(malformed.id)],
            text_splitter=SplitTextOnlySplitter(),
        )

        assert result["indexed_files"] == 0
        assert result["chunks"] == []
        assert result["skipped_files"] == [
            {"file_id": str(malformed.id), "reason": "malformed_storage"}
        ]

    def test_zero_byte_txt_decodes_but_is_skipped_because_it_has_no_chunks(self):
        record = self.create_encrypted_record(
            filename="empty.txt",
            plaintext=b"",
        )
        splitter = SplitTextOnlySplitter()

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(record.id)],
            text_splitter=splitter,
        )

        assert result["indexed_files"] == 0
        assert result["chunks"] == []
        assert result["skipped_files"] == [
            {"file_id": str(record.id), "reason": "no_chunks"}
        ]
        assert splitter.seen_texts == [""]

    def test_duplicate_file_ids_are_processed_once(self):
        record = self.create_encrypted_record(plaintext=b"deduplicated selection")
        splitter = SplitTextOnlySplitter()

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(record.id), str(record.id)],
            text_splitter=splitter,
        )

        assert result["indexed_files"] == 1
        assert result["skipped_files"] == []
        assert [chunk["page_content"] for chunk in result["chunks"]] == [
            "deduplicated selection"
        ]
        assert splitter.seen_texts == ["deduplicated selection"]

    def test_empty_file_ids_returns_empty_result_envelope(self):
        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[],
            text_splitter=SplitTextOnlySplitter(),
        )

        assert result == {"indexed_files": 0, "skipped_files": [], "chunks": []}
