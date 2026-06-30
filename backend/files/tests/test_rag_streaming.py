from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator

from core.asgi import application
from files import rag_protocol as protocol


ASK_VAULT_PATH = "/ws/ask-vault/?user_id=rag-user"

FILE_A = "11111111-1111-1111-1111-111111111111"
FILE_B = "22222222-2222-2222-2222-222222222222"


class FakeDocument:
    def __init__(self, page_content, metadata):
        self.page_content = page_content
        self.metadata = metadata


class FakeSessionIndex:
    def __init__(self, result):
        self.result = result
        self.questions = []

    def retrieve(self, question):
        self.questions.append(question)
        return self.result

    def cleanup(self):
        pass


def ask_message(question):
    return {
        protocol.FIELD_ACTION: protocol.ACTION_ASK,
        protocol.FIELD_QUESTION: question,
    }


def select_message():
    return {
        protocol.FIELD_ACTION: protocol.ACTION_SELECT,
        protocol.FIELD_FILE_IDS: [FILE_A],
    }


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


async def connect_ready_communicator(monkeypatch, retrieve_result):
    from files.consumers import AskVaultConsumer

    fake_index = FakeSessionIndex(retrieve_result)

    async def instant_ingest(self, file_ids):
        self.session_index = fake_index
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

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_INGESTING,
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_READY,
        protocol.FIELD_INDEXED_FILES: 1,
        protocol.FIELD_SKIPPED_FILES: [],
    }

    return communicator, fake_index


def test_protocol_includes_llm_failed_error_code():
    assert protocol.ERROR_LLM_FAILED == "llm_failed"


async def assert_relevant_answer_streams_tokens_then_done_and_allows_retry(
    monkeypatch,
):
    from files.consumers import AskVaultConsumer

    documents = [doc("alpha context", FILE_A), doc("beta context", FILE_B)]
    stream_calls = []

    def fake_stream_answer_tokens(self, question, retrieved_documents):
        stream_calls.append(
            {
                protocol.FIELD_QUESTION: question,
                "documents": retrieved_documents,
            }
        )
        if question == "First question?":
            return iter(["The ", "first ", "answer."])
        return iter(["The ", "second ", "answer."])

    monkeypatch.setattr(
        AskVaultConsumer,
        "stream_answer_tokens",
        fake_stream_answer_tokens,
        raising=False,
    )

    communicator, fake_index = await connect_ready_communicator(
        monkeypatch,
        {
            "answerable": True,
            "documents": documents,
            protocol.FIELD_SOURCES: [FILE_A, FILE_B],
        },
    )

    await communicator.send_json_to(ask_message("First question?"))
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "The ",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "first ",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "answer.",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
        protocol.FIELD_SOURCES: [FILE_A, FILE_B],
    }

    await communicator.send_json_to(ask_message("Second question?"))
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "The ",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "second ",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "answer.",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
        protocol.FIELD_SOURCES: [FILE_A, FILE_B],
    }

    assert fake_index.questions == ["First question?", "Second question?"]
    assert stream_calls == [
        {protocol.FIELD_QUESTION: "First question?", "documents": documents},
        {protocol.FIELD_QUESTION: "Second question?", "documents": documents},
    ]

    await communicator.disconnect()


def test_relevant_answer_streams_tokens_then_done_and_allows_retry(monkeypatch):
    async_to_sync(assert_relevant_answer_streams_tokens_then_done_and_allows_retry)(
        monkeypatch
    )


async def assert_llm_failure_after_partial_tokens_emits_error_without_done(
    monkeypatch,
):
    from files.consumers import AskVaultConsumer

    documents = [doc("alpha context", FILE_A)]
    stream_calls = []

    def fake_stream_answer_tokens(self, question, retrieved_documents):
        stream_calls.append(
            {
                protocol.FIELD_QUESTION: question,
                "documents": retrieved_documents,
            }
        )

        if question == "First question?":
            return failing_token_stream()
        return iter(["retry ", "answer"])

    def failing_token_stream():
        yield "partial "
        raise RuntimeError("simulated LLM failure")

    monkeypatch.setattr(
        AskVaultConsumer,
        "stream_answer_tokens",
        fake_stream_answer_tokens,
        raising=False,
    )

    communicator, fake_index = await connect_ready_communicator(
        monkeypatch,
        {
            "answerable": True,
            "documents": documents,
            protocol.FIELD_SOURCES: [FILE_A],
        },
    )

    await communicator.send_json_to(ask_message("First question?"))
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "partial ",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_ERROR,
        protocol.FIELD_CODE: protocol.ERROR_LLM_FAILED,
    }
    assert await communicator.receive_nothing(timeout=0.05, interval=0.01)

    await communicator.send_json_to(ask_message("Second question?"))
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "retry ",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "answer",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
        protocol.FIELD_SOURCES: [FILE_A],
    }

    assert fake_index.questions == ["First question?", "Second question?"]
    assert stream_calls == [
        {protocol.FIELD_QUESTION: "First question?", "documents": documents},
        {protocol.FIELD_QUESTION: "Second question?", "documents": documents},
    ]

    await communicator.disconnect()


def test_llm_failure_after_partial_tokens_emits_error_without_done(monkeypatch):
    async_to_sync(assert_llm_failure_after_partial_tokens_emits_error_without_done)(
        monkeypatch
    )
