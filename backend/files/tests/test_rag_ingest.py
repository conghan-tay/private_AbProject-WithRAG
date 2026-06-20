import hashlib
import tempfile
from uuid import uuid4

from django.core.files.base import File as DjangoFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.utils import setup_databases, teardown_databases

from files import rag_protocol as protocol
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
    return sorted(
        skipped_files,
        key=lambda item: (item[protocol.FIELD_FILE_ID], item[protocol.FIELD_REASON]),
    )


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
        file_type=protocol.SUPPORTED_TEXT_MIME_TYPE,
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

        assert result[protocol.FIELD_INDEXED_FILES] == 1
        assert result[protocol.FIELD_SKIPPED_FILES] == []
        assert splitter.seen_texts == ["alpha facts\n\nbeta facts"]
        assert [
            chunk[protocol.FIELD_PAGE_CONTENT]
            for chunk in result[protocol.FIELD_CHUNKS]
        ] == [
            "alpha facts",
            "beta facts",
        ]
        assert result[protocol.FIELD_CHUNKS][0][protocol.FIELD_METADATA] == {
            protocol.FIELD_USER_ID: "rag-user",
            protocol.FIELD_FILE_ID: str(record.id),
            protocol.FIELD_STORAGE_FILE_ID: str(record.id),
            protocol.FIELD_ORIGINAL_FILENAME: "case-notes.txt",
            protocol.FIELD_FILE_TYPE: protocol.SUPPORTED_TEXT_MIME_TYPE,
            protocol.FIELD_CHUNK_INDEX: 0,
        }
        assert (
            result[protocol.FIELD_CHUNKS][1][protocol.FIELD_METADATA][
                protocol.FIELD_CHUNK_INDEX
            ]
            == 1
        )

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

        assert result[protocol.FIELD_INDEXED_FILES] == 1
        assert result[protocol.FIELD_SKIPPED_FILES] == []
        assert len(result[protocol.FIELD_CHUNKS]) > 1

        for chunk_index, chunk in enumerate(result[protocol.FIELD_CHUNKS]):
            assert chunk[protocol.FIELD_PAGE_CONTENT]
            assert len(chunk[protocol.FIELD_PAGE_CONTENT]) <= 40
            assert chunk[protocol.FIELD_METADATA] == {
                protocol.FIELD_USER_ID: "rag-user",
                protocol.FIELD_FILE_ID: str(record.id),
                protocol.FIELD_STORAGE_FILE_ID: str(record.id),
                protocol.FIELD_ORIGINAL_FILENAME: "long-notes.txt",
                protocol.FIELD_FILE_TYPE: protocol.SUPPORTED_TEXT_MIME_TYPE,
                protocol.FIELD_CHUNK_INDEX: chunk_index,
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

        assert result[protocol.FIELD_INDEXED_FILES] == 0
        assert result[protocol.FIELD_CHUNKS] == []
        expected_skips = [
            {
                protocol.FIELD_FILE_ID: str(missing_id),
                protocol.FIELD_REASON: protocol.SKIP_NOT_FOUND_OR_NOT_OWNED,
            },
            {
                protocol.FIELD_FILE_ID: str(other_record.id),
                protocol.FIELD_REASON: protocol.SKIP_NOT_FOUND_OR_NOT_OWNED,
            },
        ]
        assert sorted_skips(result[protocol.FIELD_SKIPPED_FILES]) == sorted_skips(
            expected_skips
        )
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

        assert result[protocol.FIELD_INDEXED_FILES] == 0
        assert result[protocol.FIELD_CHUNKS] == []
        assert result[protocol.FIELD_SKIPPED_FILES] == [
            {
                protocol.FIELD_FILE_ID: str(record.id),
                protocol.FIELD_REASON: protocol.SKIP_UNSUPPORTED_TYPE,
                protocol.FIELD_FILE_TYPE: "application/pdf",
            }
        ]

    def test_invalid_utf8_txt_is_skipped_as_unsupported_encoding(self):
        record = self.create_encrypted_record(
            filename="bad.txt",
            file_type=protocol.SUPPORTED_TEXT_MIME_TYPE,
            plaintext=b"valid prefix \xff invalid utf8",
        )

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(record.id)],
            text_splitter=SplitTextOnlySplitter(),
        )

        assert result[protocol.FIELD_INDEXED_FILES] == 0
        assert result[protocol.FIELD_CHUNKS] == []
        assert result[protocol.FIELD_SKIPPED_FILES] == [
            {
                protocol.FIELD_FILE_ID: str(record.id),
                protocol.FIELD_REASON: protocol.SKIP_UNSUPPORTED_ENCODING,
            }
        ]

    def test_invalid_aes_gcm_tag_is_skipped_as_malformed_storage(self):
        record = self.create_encrypted_record(
            filename="corrupt.txt",
            file_type=protocol.SUPPORTED_TEXT_MIME_TYPE,
            plaintext=b"valid utf8 but wrong iv",
        )
        record.encryption_iv = b"\x01" * 12
        record.save(update_fields=["encryption_iv"])

        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[str(record.id)],
            text_splitter=SplitTextOnlySplitter(),
        )

        assert result[protocol.FIELD_INDEXED_FILES] == 0
        assert result[protocol.FIELD_CHUNKS] == []
        assert result[protocol.FIELD_SKIPPED_FILES] == [
            {
                protocol.FIELD_FILE_ID: str(record.id),
                protocol.FIELD_REASON: protocol.SKIP_MALFORMED_STORAGE,
            }
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

        assert result[protocol.FIELD_INDEXED_FILES] == 1
        assert result[protocol.FIELD_SKIPPED_FILES] == []
        assert result[protocol.FIELD_CHUNKS] == [
            {
                protocol.FIELD_PAGE_CONTENT: "reference backed text",
                protocol.FIELD_METADATA: {
                    protocol.FIELD_USER_ID: "rag-user",
                    protocol.FIELD_FILE_ID: str(reference.id),
                    protocol.FIELD_STORAGE_FILE_ID: str(original.id),
                    protocol.FIELD_ORIGINAL_FILENAME: "duplicate-name.txt",
                    protocol.FIELD_FILE_TYPE: protocol.SUPPORTED_TEXT_MIME_TYPE,
                    protocol.FIELD_CHUNK_INDEX: 0,
                },
            }
        ]

    def test_malformed_reference_storage_is_skipped(self):
        malformed = File.objects.create(
            user_id="rag-user",
            file=None,
            original_filename="orphan-reference.txt",
            file_type=protocol.SUPPORTED_TEXT_MIME_TYPE,
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

        assert result[protocol.FIELD_INDEXED_FILES] == 0
        assert result[protocol.FIELD_CHUNKS] == []
        assert result[protocol.FIELD_SKIPPED_FILES] == [
            {
                protocol.FIELD_FILE_ID: str(malformed.id),
                protocol.FIELD_REASON: protocol.SKIP_MALFORMED_STORAGE,
            }
        ]

    def test_owned_original_with_missing_storage_file_is_skipped_as_malformed_storage(self):
        malformed = File.objects.create(
            user_id="rag-user",
            file=None,
            original_filename="missing-storage.txt",
            file_type=protocol.SUPPORTED_TEXT_MIME_TYPE,
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

        assert result[protocol.FIELD_INDEXED_FILES] == 0
        assert result[protocol.FIELD_CHUNKS] == []
        assert result[protocol.FIELD_SKIPPED_FILES] == [
            {
                protocol.FIELD_FILE_ID: str(malformed.id),
                protocol.FIELD_REASON: protocol.SKIP_MALFORMED_STORAGE,
            }
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

        assert result[protocol.FIELD_INDEXED_FILES] == 0
        assert result[protocol.FIELD_CHUNKS] == []
        assert result[protocol.FIELD_SKIPPED_FILES] == [
            {
                protocol.FIELD_FILE_ID: str(record.id),
                protocol.FIELD_REASON: protocol.SKIP_NO_CHUNKS,
            }
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

        assert result[protocol.FIELD_INDEXED_FILES] == 1
        assert result[protocol.FIELD_SKIPPED_FILES] == []
        assert [
            chunk[protocol.FIELD_PAGE_CONTENT]
            for chunk in result[protocol.FIELD_CHUNKS]
        ] == [
            "deduplicated selection"
        ]
        assert splitter.seen_texts == ["deduplicated selection"]

    def test_empty_file_ids_returns_empty_result_envelope(self):
        result = self.service().ingest_files(
            user_id="rag-user",
            file_ids=[],
            text_splitter=SplitTextOnlySplitter(),
        )

        assert result == {
            protocol.FIELD_INDEXED_FILES: 0,
            protocol.FIELD_SKIPPED_FILES: [],
            protocol.FIELD_CHUNKS: [],
        }
