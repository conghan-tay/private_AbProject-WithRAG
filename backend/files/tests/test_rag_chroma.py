import asyncio
import importlib
import sys
import types

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings

from core.asgi import application
from files import rag_protocol as protocol


ASK_VAULT_PATH = "/ws/ask-vault/?user_id=rag-user"


class FakeEmbedding:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.instances.append(self)


class FakeChroma:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.added_documents = None
        self.added_ids = None
        self.delete_collection_calls = 0
        self.__class__.instances.append(self)

    def add_documents(self, documents, ids=None):
        self.added_documents = documents
        self.added_ids = ids

    def delete_collection(self):
        self.delete_collection_calls += 1


class FakeEphemeralClient:
    instances = []

    def __init__(self):
        self.__class__.instances.append(self)


def install_fake_rag_dependencies(monkeypatch):
    FakeEmbedding.instances = []
    FakeChroma.instances = []
    FakeEphemeralClient.instances = []

    chromadb = types.ModuleType("chromadb")
    chromadb.EphemeralClient = FakeEphemeralClient
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)

    langchain_chroma = types.ModuleType("langchain_chroma")
    langchain_chroma.Chroma = FakeChroma
    monkeypatch.setitem(sys.modules, "langchain_chroma", langchain_chroma)

    langchain_openai = types.ModuleType("langchain_openai")
    langchain_openai.OpenAIEmbeddings = FakeEmbedding
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)


def import_rag_index(monkeypatch):
    install_fake_rag_dependencies(monkeypatch)
    sys.modules.pop("files.services.rag_index", None)
    return importlib.import_module("files.services.rag_index")


def sample_chunks():
    return [
        {
            protocol.FIELD_PAGE_CONTENT: "alpha indicators",
            protocol.FIELD_METADATA: {
                protocol.FIELD_FILE_ID: "11111111-1111-1111-1111-111111111111",
                protocol.FIELD_STORAGE_FILE_ID: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                protocol.FIELD_ORIGINAL_FILENAME: "case-a.txt",
                protocol.FIELD_CHUNK_INDEX: 0,
            },
        },
        {
            protocol.FIELD_PAGE_CONTENT: "beta indicators",
            protocol.FIELD_METADATA: {
                protocol.FIELD_FILE_ID: "11111111-1111-1111-1111-111111111111",
                protocol.FIELD_STORAGE_FILE_ID: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                protocol.FIELD_ORIGINAL_FILENAME: "case-a.txt",
                protocol.FIELD_CHUNK_INDEX: 1,
            },
        },
        {
            protocol.FIELD_PAGE_CONTENT: "gamma indicators",
            protocol.FIELD_METADATA: {
                protocol.FIELD_FILE_ID: "22222222-2222-2222-2222-222222222222",
                protocol.FIELD_STORAGE_FILE_ID: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                protocol.FIELD_ORIGINAL_FILENAME: "case-b.txt",
                protocol.FIELD_CHUNK_INDEX: 0,
            },
        },
    ]


def test_rag_embedding_settings_defaults_are_configured():
    assert settings.OPENAI_API_KEY is None
    assert settings.RAG_EMBEDDING_MODEL == "text-embedding-3-small"
    assert settings.RAG_EMBEDDING_DIMENSIONS == 1536


def test_session_index_uses_ephemeral_chroma_and_openai_embedding_settings(monkeypatch):
    rag_index = import_rag_index(monkeypatch)

    index = rag_index.RagSessionIndex(session_id="session-123")

    assert len(FakeEphemeralClient.instances) == 1
    assert len(FakeEmbedding.instances) == 1
    assert FakeEmbedding.instances[0].kwargs == {
        "model": settings.RAG_EMBEDDING_MODEL,
        "dimensions": settings.RAG_EMBEDDING_DIMENSIONS,
    }
    assert len(FakeChroma.instances) == 1
    assert FakeChroma.instances[0].kwargs == {
        "client": FakeEphemeralClient.instances[0],
        "collection_name": "askvault-session-123",
        "embedding_function": FakeEmbedding.instances[0],
        "collection_configuration": {"hnsw": {"space": "cosine"}},
    }
    assert "persist_directory" not in FakeChroma.instances[0].kwargs
    assert index.vector_store is FakeChroma.instances[0]
    assert index.chroma_client is FakeEphemeralClient.instances[0]


def test_session_index_adds_documents_with_metadata_and_stable_ids(monkeypatch):
    rag_index = import_rag_index(monkeypatch)
    index = rag_index.RagSessionIndex(session_id="session-123")

    index.index_chunks(sample_chunks())

    vector_store = FakeChroma.instances[0]
    assert vector_store.added_ids == [
        "11111111-1111-1111-1111-111111111111:0",
        "11111111-1111-1111-1111-111111111111:1",
        "22222222-2222-2222-2222-222222222222:0",
    ]
    assert [doc.page_content for doc in vector_store.added_documents] == [
        "alpha indicators",
        "beta indicators",
        "gamma indicators",
    ]
    assert [doc.metadata for doc in vector_store.added_documents] == [
        chunk[protocol.FIELD_METADATA] for chunk in sample_chunks()
    ]


def test_session_index_cleanup_deletes_collection_and_drops_references(monkeypatch):
    rag_index = import_rag_index(monkeypatch)
    index = rag_index.RagSessionIndex(session_id="session-123")
    vector_store = FakeChroma.instances[0]

    index.cleanup()

    assert vector_store.delete_collection_calls == 1
    assert index.vector_store is None
    assert index.chroma_client is None


async def assert_disconnect_cleans_up_session_index(monkeypatch):
    cleanup_calls = []

    class FakeSessionIndex:
        def cleanup(self):
            cleanup_calls.append("cleanup")

    async def successful_ingest(self, file_ids):
        self.session_index = FakeSessionIndex()
        return {
            protocol.FIELD_INDEXED_FILES: len(file_ids),
            protocol.FIELD_SKIPPED_FILES: [],
        }

    from files.consumers import AskVaultConsumer

    monkeypatch.setattr(AskVaultConsumer, "run_ingest", successful_ingest)

    communicator = WebsocketCommunicator(application, ASK_VAULT_PATH)
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(
        {
            protocol.FIELD_ACTION: protocol.ACTION_SELECT,
            protocol.FIELD_FILE_IDS: ["11111111-1111-1111-1111-111111111111"],
        }
    )
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_INGESTING,
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_READY,
        protocol.FIELD_INDEXED_FILES: 1,
        protocol.FIELD_SKIPPED_FILES: [],
    }

    await communicator.disconnect()
    await asyncio.sleep(0)

    assert cleanup_calls == ["cleanup"]


def test_disconnect_cleans_up_session_index(monkeypatch):
    async_to_sync(assert_disconnect_cleans_up_session_index)(monkeypatch)
