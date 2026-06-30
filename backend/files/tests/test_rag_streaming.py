from asgiref.sync import async_to_sync

from files import rag_protocol as protocol
from files.tests._rag_test_helpers import (
    FILE_A,
    FILE_B,
    ask_message,
    connect_ready_communicator,
    doc,
)


class CloseTrackingIterator:
    def __init__(self, values, failure=None):
        self.values = iter(values)
        self.failure = failure
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.values)
        except StopIteration:
            if self.failure is not None:
                raise self.failure
            raise

    def close(self):
        self.closed = True


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


def test_next_stream_token_uses_private_end_sentinel():
    from files import consumers

    assert consumers.next_stream_token(iter([])) is consumers._STREAM_END
    assert consumers.next_stream_token(iter([None])) is None
    assert consumers.next_stream_token(iter(["token"])) == "token"


async def assert_generate_answer_messages_closes_iterator_on_success():
    from files.consumers import AskVaultConsumer

    token_iterator = CloseTrackingIterator(["token"])
    consumer = AskVaultConsumer()
    consumer.rag_session_id = "session-close-success"
    consumer.stream_answer_tokens = lambda question, documents: token_iterator

    messages = [
        message
        async for message in consumer.generate_answer_messages(
            "Question?",
            [doc("alpha context", FILE_A)],
            [FILE_A],
        )
    ]

    assert messages == [
        {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
            protocol.FIELD_DATA: "token",
        },
        {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
            protocol.FIELD_SOURCES: [FILE_A],
        },
    ]
    assert token_iterator.closed is True


def test_generate_answer_messages_closes_iterator_on_success():
    async_to_sync(assert_generate_answer_messages_closes_iterator_on_success)()


async def assert_generate_answer_messages_closes_iterator_on_llm_failure():
    from files.consumers import AskVaultConsumer

    token_iterator = CloseTrackingIterator(
        ["partial "],
        failure=RuntimeError("simulated stream failure"),
    )
    consumer = AskVaultConsumer()
    consumer.rag_session_id = "session-close-failure"
    consumer.stream_answer_tokens = lambda question, documents: token_iterator

    messages = [
        message
        async for message in consumer.generate_answer_messages(
            "Question?",
            [doc("alpha context", FILE_A)],
            [FILE_A],
        )
    ]

    assert messages == [
        {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
            protocol.FIELD_DATA: "partial ",
        },
        {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_ERROR,
            protocol.FIELD_CODE: protocol.ERROR_LLM_FAILED,
        },
    ]
    assert token_iterator.closed is True


def test_generate_answer_messages_closes_iterator_on_llm_failure():
    async_to_sync(assert_generate_answer_messages_closes_iterator_on_llm_failure)()


async def assert_complete_answer_notifies_llm_failed_on_unhandled_answer_exception():
    from files.consumers import AskVaultConsumer

    consumer = AskVaultConsumer()
    consumer.rag_session_id = "session-unhandled-answer-failure"
    consumer.state = protocol.STATE_ANSWERING
    sent_errors = []

    async def failing_run_answer(question):
        raise RuntimeError("simulated run_answer failure")
        if False:
            yield

    async def capture_error(code):
        sent_errors.append(code)

    consumer.run_answer = failing_run_answer
    consumer.send_error = capture_error

    await consumer.complete_answer("Question?")

    assert sent_errors == [protocol.ERROR_LLM_FAILED]
    assert consumer.state == protocol.STATE_READY


def test_complete_answer_notifies_llm_failed_on_unhandled_answer_exception():
    async_to_sync(
        assert_complete_answer_notifies_llm_failed_on_unhandled_answer_exception
    )()


async def assert_llm_stream_next_runs_thread_sensitive_false(monkeypatch):
    from files import consumers
    from files.consumers import AskVaultConsumer

    calls = []
    real_sync_to_async = consumers.sync_to_async

    def recording_sync_to_async(func, thread_sensitive=True):
        calls.append(
            {
                "name": getattr(func, "__name__", repr(func)),
                "thread_sensitive": thread_sensitive,
            }
        )
        return real_sync_to_async(func, thread_sensitive=thread_sensitive)

    monkeypatch.setattr(consumers, "sync_to_async", recording_sync_to_async)

    token_iterator = CloseTrackingIterator(["token"])
    consumer = AskVaultConsumer()
    consumer.rag_session_id = "session-thread-sensitive"
    consumer.stream_answer_tokens = lambda question, documents: token_iterator

    messages = [
        message
        async for message in consumer.generate_answer_messages(
            "Question?",
            [doc("alpha context", FILE_A)],
            [FILE_A],
        )
    ]

    assert messages[-1] == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
        protocol.FIELD_SOURCES: [FILE_A],
    }
    assert {
        "name": "next_stream_token",
        "thread_sensitive": False,
    } in calls
    assert {
        "name": "close",
        "thread_sensitive": False,
    } in calls


def test_llm_stream_next_runs_thread_sensitive_false(monkeypatch):
    async_to_sync(assert_llm_stream_next_runs_thread_sensitive_false)(monkeypatch)


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
