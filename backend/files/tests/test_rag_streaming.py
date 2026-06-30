from asgiref.sync import async_to_sync

from files import rag_protocol as protocol
from files.tests._rag_test_helpers import (
    FILE_A,
    FILE_B,
    ask_message,
    connect_ready_communicator,
    doc,
)


def test_protocol_includes_llm_failed_error_code():
    assert protocol.ERROR_LLM_FAILED == "llm_failed"


def assert_stream_answer_tokens_hook_exists(consumer_class):
    assert hasattr(consumer_class, "stream_answer_tokens"), (
        "Step 11 must expose AskVaultConsumer.stream_answer_tokens(self, "
        "question, retrieved_documents) returning a sync iterator of token "
        "strings."
    )


def test_consumer_exposes_stream_answer_tokens_hook():
    from files.consumers import AskVaultConsumer

    assert_stream_answer_tokens_hook_exists(AskVaultConsumer)


async def assert_relevant_answer_streams_tokens_then_done_and_allows_retry(
    monkeypatch,
):
    from files.consumers import AskVaultConsumer

    assert_stream_answer_tokens_hook_exists(AskVaultConsumer)

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

    assert_stream_answer_tokens_hook_exists(AskVaultConsumer)

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


async def assert_llm_failure_before_any_token_emits_error_without_done(
    monkeypatch,
):
    from files.consumers import AskVaultConsumer

    assert_stream_answer_tokens_hook_exists(AskVaultConsumer)

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
            return failing_immediately()
        return iter(["retry ", "answer"])

    def failing_immediately():
        if False:
            yield
        raise RuntimeError("simulated LLM failure before first token")

    monkeypatch.setattr(
        AskVaultConsumer,
        "stream_answer_tokens",
        fake_stream_answer_tokens,
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


def test_llm_failure_before_any_token_emits_error_without_done(monkeypatch):
    async_to_sync(assert_llm_failure_before_any_token_emits_error_without_done)(
        monkeypatch
    )
