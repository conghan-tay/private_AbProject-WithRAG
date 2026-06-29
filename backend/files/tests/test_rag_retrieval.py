import importlib
import sys
import types

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import override_settings

from core.asgi import application
from files import rag_protocol as protocol


ASK_VAULT_PATH = "/ws/ask-vault/?user_id=rag-user"

FILE_A = "11111111-1111-1111-1111-111111111111"
FILE_B = "22222222-2222-2222-2222-222222222222"


class FakeEmbedding:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeEphemeralClient:
    pass


class FakeDocument:
    def __init__(self, page_content, metadata):
        self.page_content = page_content
        self.metadata = metadata


class FakeRetriever:
    def __init__(self, documents):
        self.documents = documents
        self.invocations = []

    def invoke(self, query):
        self.invocations.append(query)
        return self.documents


class FakeChroma:
    instances = []
    similarity_results = []
    retriever_documents = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.similarity_calls = []
        self.retriever_calls = []
        self.retriever = None
        self.__class__.instances.append(self)

    def add_documents(self, documents, ids=None):
        self.added_documents = documents
        self.added_ids = ids

    def similarity_search_with_score(self, query, **kwargs):
        self.similarity_calls.append({"query": query, **kwargs})
        return self.__class__.similarity_results

    def as_retriever(self, **kwargs):
        self.retriever_calls.append(kwargs)
        self.retriever = FakeRetriever(self.__class__.retriever_documents)
        return self.retriever

    def delete_collection(self):
        pass


def install_fake_rag_dependencies(monkeypatch):
    FakeChroma.instances = []
    FakeChroma.similarity_results = []
    FakeChroma.retriever_documents = []

    chromadb = types.ModuleType("chromadb")
    chromadb.EphemeralClient = FakeEphemeralClient
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)

    langchain_chroma = types.ModuleType("langchain_chroma")
    langchain_chroma.Chroma = FakeChroma
    monkeypatch.setitem(sys.modules, "langchain_chroma", langchain_chroma)

    langchain_openai = types.ModuleType("langchain_openai")
    langchain_openai.OpenAIEmbeddings = FakeEmbedding
    monkeypatch.setitem(sys.modules, "langchain_openai", langchain_openai)

    langchain_core = types.ModuleType("langchain_core")
    langchain_core_documents = types.ModuleType("langchain_core.documents")
    langchain_core_documents.Document = FakeDocument
    langchain_core.documents = langchain_core_documents
    monkeypatch.setitem(sys.modules, "langchain_core", langchain_core)
    monkeypatch.setitem(sys.modules, "langchain_core.documents", langchain_core_documents)


def import_rag_index_with_fakes(monkeypatch):
    install_fake_rag_dependencies(monkeypatch)
    sys.modules.pop("files.services.rag_index", None)
    return importlib.import_module("files.services.rag_index")


def doc(text, file_id, chunk_index=0):
    return FakeDocument(
        page_content=text,
        metadata={
            protocol.FIELD_USER_ID: "rag-user",
            protocol.FIELD_FILE_ID: file_id,
            protocol.FIELD_STORAGE_FILE_ID: file_id,
            protocol.FIELD_ORIGINAL_FILENAME: f"{file_id}.txt",
            protocol.FIELD_FILE_TYPE: protocol.SUPPORTED_TEXT_MIME_TYPE,
            protocol.FIELD_CHUNK_INDEX: chunk_index,
        },
    )


def test_rag_retrieval_settings_defaults_are_configured():
    assert settings.RAG_RETRIEVAL_K == 4
    assert settings.RAG_RETRIEVAL_FETCH_K == 12
    assert settings.RAG_MAX_CONTEXT_CHUNKS == 4
    assert settings.RAG_MAX_DISTANCE == 0.35


@override_settings(
    RAG_RETRIEVAL_K=4,
    RAG_RETRIEVAL_FETCH_K=12,
    RAG_MAX_CONTEXT_CHUNKS=4,
    RAG_MAX_DISTANCE=0.35,
)
def test_retrieve_treats_chroma_scores_as_distances_and_accepts_low_score(
    monkeypatch,
):
    rag_index = import_rag_index_with_fakes(monkeypatch)
    threshold_doc = doc("alpha indicators", FILE_A)
    context_docs = [
        doc("beta context", FILE_B, 0),
        doc("alpha context", FILE_A, 0),
    ]
    FakeChroma.similarity_results = [(threshold_doc, 0.12)]
    FakeChroma.retriever_documents = context_docs

    index = rag_index.RagSessionIndex(session_id="session-123")

    result = index.retrieve("What indicators are present?")

    vector_store = FakeChroma.instances[0]
    assert vector_store.similarity_calls == [
        {"query": "What indicators are present?"}
    ]
    assert vector_store.retriever_calls == [
        {
            "search_type": "mmr",
            "search_kwargs": {
                "k": settings.RAG_RETRIEVAL_K,
                "fetch_k": settings.RAG_RETRIEVAL_FETCH_K,
            },
        }
    ]
    assert vector_store.retriever.invocations == ["What indicators are present?"]
    assert result == {
        "answerable": True,
        "documents": context_docs,
        protocol.FIELD_SOURCES: [FILE_A, FILE_B],
    }


@override_settings(
    RAG_RETRIEVAL_K=4,
    RAG_RETRIEVAL_FETCH_K=12,
    RAG_MAX_CONTEXT_CHUNKS=4,
    RAG_MAX_DISTANCE=0.35,
)
def test_retrieve_returns_no_answer_when_top_distance_exceeds_threshold(
    monkeypatch,
):
    rag_index = import_rag_index_with_fakes(monkeypatch)
    FakeChroma.similarity_results = [
        (doc("unrelated result", FILE_A), settings.RAG_MAX_DISTANCE + 0.01)
    ]
    FakeChroma.retriever_documents = [doc("should not be used", FILE_A)]

    index = rag_index.RagSessionIndex(session_id="session-123")

    result = index.retrieve("Unrelated question?")

    vector_store = FakeChroma.instances[0]
    assert vector_store.similarity_calls == [{"query": "Unrelated question?"}]
    assert vector_store.retriever_calls == []
    assert result == {
        "answerable": False,
        "documents": [],
        protocol.FIELD_SOURCES: [],
    }


@override_settings(
    RAG_RETRIEVAL_K=4,
    RAG_RETRIEVAL_FETCH_K=12,
    RAG_MAX_CONTEXT_CHUNKS=2,
    RAG_MAX_DISTANCE=0.35,
)
def test_retrieve_caps_context_docs_before_deriving_sorted_sources(monkeypatch):
    rag_index = import_rag_index_with_fakes(monkeypatch)
    FakeChroma.similarity_results = [(doc("threshold result", FILE_A), 0.12)]
    first = doc("source b first", FILE_B, 0)
    second = doc("source a second", FILE_A, 0)
    ignored = doc("source b ignored by context cap", FILE_B, 1)
    FakeChroma.retriever_documents = [first, second, ignored]

    index = rag_index.RagSessionIndex(session_id="session-123")

    result = index.retrieve("What sources should be cited?")

    assert result == {
        "answerable": True,
        "documents": [first, second],
        protocol.FIELD_SOURCES: [FILE_A, FILE_B],
    }


class FakeSessionIndex:
    def __init__(self, result):
        self.result = result
        self.questions = []

    def retrieve(self, question):
        self.questions.append(question)
        return self.result

    def cleanup(self):
        pass


async def connect_ready_communicator(monkeypatch, retrieve_result):
    from files.consumers import AskVaultConsumer

    async def instant_ingest(self, file_ids):
        self.session_index = FakeSessionIndex(retrieve_result)
        return {
            protocol.FIELD_INDEXED_FILES: len(file_ids),
            protocol.FIELD_SKIPPED_FILES: [],
        }

    monkeypatch.setattr(AskVaultConsumer, "run_ingest", instant_ingest)

    communicator = WebsocketCommunicator(application, ASK_VAULT_PATH)
    connected, _ = await communicator.connect()
    assert connected is True
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_CONNECTED_NO_DOCUMENTS,
    }

    await communicator.send_json_to(
        {
            protocol.FIELD_ACTION: protocol.ACTION_SELECT,
            protocol.FIELD_FILE_IDS: [FILE_A],
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
    return communicator


async def assert_ws_no_answer_does_not_call_answer_generation(monkeypatch):
    from files.consumers import AskVaultConsumer

    answer_generation_calls = []

    async def forbidden_answer_generation(self, question, documents, sources):
        answer_generation_calls.append(
            {
                protocol.FIELD_QUESTION: question,
                "documents": documents,
                protocol.FIELD_SOURCES: sources,
            }
        )
        yield {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
            protocol.FIELD_SOURCES: sources,
        }

    monkeypatch.setattr(
        AskVaultConsumer,
        "generate_answer_messages",
        forbidden_answer_generation,
        raising=False,
    )

    communicator = await connect_ready_communicator(
        monkeypatch,
        {
            "answerable": False,
            "documents": [],
            protocol.FIELD_SOURCES: [],
        },
    )

    await communicator.send_json_to(
        {
            protocol.FIELD_ACTION: protocol.ACTION_ASK,
            protocol.FIELD_QUESTION: "What is outside the documents?",
        }
    )

    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_NO_ANSWER,
        protocol.FIELD_REASON: "not_in_documents",
    }
    assert answer_generation_calls == []
    await communicator.disconnect()


def test_ws_no_answer_does_not_call_answer_generation(monkeypatch):
    async_to_sync(assert_ws_no_answer_does_not_call_answer_generation)(monkeypatch)


async def assert_ws_relevant_answer_terminates_with_sorted_sources(monkeypatch):
    communicator = await connect_ready_communicator(
        monkeypatch,
        {
            "answerable": True,
            "documents": [doc("alpha", FILE_A), doc("beta", FILE_B)],
            protocol.FIELD_SOURCES: [FILE_A, FILE_B],
        },
    )

    await communicator.send_json_to(
        {
            protocol.FIELD_ACTION: protocol.ACTION_ASK,
            protocol.FIELD_QUESTION: "What is in the selected files?",
        }
    )

    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
        protocol.FIELD_SOURCES: [FILE_A, FILE_B],
    }
    await communicator.disconnect()


def test_ws_relevant_answer_terminates_with_sorted_sources(monkeypatch):
    async_to_sync(assert_ws_relevant_answer_terminates_with_sorted_sources)(
        monkeypatch
    )
