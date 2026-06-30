"""Tests-first contract for the future AskVaultConsumer.

These tests are intentionally red until Step 2 wires Channels routing and adds
files.consumers.AskVaultConsumer with patchable run_ingest/run_answer hooks.
"""

import asyncio
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator

from core.asgi import application
from files import rag_protocol as protocol


ASK_VAULT_PATH = "/ws/ask-vault/"


def ws_path(user_id="rag-user"):
    return f"{ASK_VAULT_PATH}?user_id={user_id}"


def select_message(file_id=None):
    return {
        protocol.FIELD_ACTION: protocol.ACTION_SELECT,
        protocol.FIELD_FILE_IDS: [str(file_id or uuid4())],
    }


def ask_message(question="What is in the file?"):
    return {
        protocol.FIELD_ACTION: protocol.ACTION_ASK,
        protocol.FIELD_QUESTION: question,
    }


def assert_error(message, code):
    assert message[protocol.FIELD_TYPE] == protocol.MESSAGE_TYPE_ERROR
    assert message[protocol.FIELD_CODE] == code


def test_ask_vault_protocol_constants_match_documented_wire_contract():
    assert protocol.ACTION_SELECT == "select"
    assert protocol.ACTION_ASK == "ask"

    assert protocol.FIELD_ACTION == "action"
    assert protocol.FIELD_CODE == "code"
    assert protocol.FIELD_DATA == "data"
    assert protocol.FIELD_FILE_IDS == "file_ids"
    assert protocol.FIELD_INDEXED_FILES == "indexed_files"
    assert protocol.FIELD_QUESTION == "question"
    assert protocol.FIELD_REASON == "reason"
    assert protocol.FIELD_SKIPPED_FILES == "skipped_files"
    assert protocol.FIELD_SOURCES == "sources"
    assert protocol.FIELD_STATE == "state"
    assert protocol.FIELD_TYPE == "type"

    assert protocol.MESSAGE_TYPE_DONE == "done"
    assert protocol.MESSAGE_TYPE_ERROR == "error"
    assert protocol.MESSAGE_TYPE_NO_ANSWER == "no_answer"
    assert protocol.MESSAGE_TYPE_READY == "ready"
    assert protocol.MESSAGE_TYPE_STATUS == "status"
    assert protocol.MESSAGE_TYPE_TOKEN == "token"

    assert protocol.REASON_NOT_IN_DOCUMENTS == "not_in_documents"

    assert protocol.STATE_ANSWERING == "answering"
    assert protocol.STATE_CONNECTED_NO_DOCUMENTS == "connected_no_documents"
    assert protocol.STATE_DISCONNECTED == "disconnected"
    assert protocol.STATE_INGESTING == "ingesting"
    assert protocol.STATE_READY == "ready"

    assert protocol.ERROR_ALREADY_SELECTED == "already_selected"
    assert protocol.ERROR_BAD_REQUEST == "bad_request"
    assert protocol.ERROR_BUSY == "busy"
    assert protocol.ERROR_NO_DOCUMENTS == "no_documents"
    assert protocol.ERROR_NOT_READY == "not_ready"

    assert protocol.CLOSE_CODE_MISSING_USER_ID == 4401
    assert protocol.CLOSE_CODE_BLANK_USER_ID == 4400


def patch_consumer_hook(monkeypatch, name, replacement):
    from files.consumers import AskVaultConsumer

    monkeypatch.setattr(AskVaultConsumer, name, replacement)


async def connect_communicator(path=ws_path()):
    communicator = WebsocketCommunicator(application, path)
    connected, detail = await communicator.connect()
    return communicator, connected, detail


async def connect_ready_communicator(monkeypatch):
    async def instant_ingest(self, file_ids):
        return {
            protocol.FIELD_INDEXED_FILES: len(file_ids),
            protocol.FIELD_SKIPPED_FILES: [],
        }

    patch_consumer_hook(monkeypatch, "run_ingest", instant_ingest)
    communicator, connected, _ = await connect_communicator()
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
    return communicator


async def assert_disconnects_with_code(path, expected_code):
    communicator, connected, detail = await connect_communicator(path)

    assert connected is False
    assert detail == expected_code


async def assert_connected_initial_status():
    communicator, connected, _ = await connect_communicator()

    assert connected is True
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_CONNECTED_NO_DOCUMENTS,
    }

    await communicator.disconnect()


async def assert_protocol_error(payload, expected_code):
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    if isinstance(payload, str):
        await communicator.send_to(text_data=payload)
    else:
        await communicator.send_json_to(payload)

    assert_error(await communicator.receive_json_from(), expected_code)
    await communicator.disconnect()


async def assert_ready_protocol_error(monkeypatch, payload, expected_code):
    communicator = await connect_ready_communicator(monkeypatch)

    await communicator.send_json_to(payload)

    assert_error(await communicator.receive_json_from(), expected_code)
    await communicator.disconnect()


def test_missing_user_id_closes_with_4401():
    async_to_sync(assert_disconnects_with_code)(
        ASK_VAULT_PATH,
        protocol.CLOSE_CODE_MISSING_USER_ID,
    )


def test_blank_user_id_closes_with_4400():
    async_to_sync(assert_disconnects_with_code)(
        ws_path("   "),
        protocol.CLOSE_CODE_BLANK_USER_ID,
    )


def test_valid_user_id_accepts_and_sends_initial_state():
    async_to_sync(assert_connected_initial_status)()


def test_malformed_json_returns_bad_request():
    async_to_sync(assert_protocol_error)("{", protocol.ERROR_BAD_REQUEST)


def test_unknown_action_returns_bad_request():
    async_to_sync(assert_protocol_error)(
        {protocol.FIELD_ACTION: "dance"},
        protocol.ERROR_BAD_REQUEST,
    )


def test_select_with_missing_file_ids_returns_bad_request():
    async_to_sync(assert_protocol_error)(
        {protocol.FIELD_ACTION: protocol.ACTION_SELECT},
        protocol.ERROR_BAD_REQUEST,
    )


def test_select_with_empty_file_ids_returns_bad_request():
    async_to_sync(assert_protocol_error)(
        {protocol.FIELD_ACTION: protocol.ACTION_SELECT, protocol.FIELD_FILE_IDS: []},
        protocol.ERROR_BAD_REQUEST,
    )


def test_select_with_non_list_file_ids_returns_bad_request():
    async_to_sync(assert_protocol_error)(
        {
            protocol.FIELD_ACTION: protocol.ACTION_SELECT,
            protocol.FIELD_FILE_IDS: str(uuid4()),
        },
        protocol.ERROR_BAD_REQUEST,
    )


def test_select_with_invalid_uuid_file_id_returns_bad_request():
    async_to_sync(assert_protocol_error)(
        {
            protocol.FIELD_ACTION: protocol.ACTION_SELECT,
            protocol.FIELD_FILE_IDS: ["not-a-uuid"],
        },
        protocol.ERROR_BAD_REQUEST,
    )


def test_ask_with_missing_question_returns_bad_request(monkeypatch):
    async_to_sync(assert_ready_protocol_error)(
        monkeypatch,
        {protocol.FIELD_ACTION: protocol.ACTION_ASK},
        protocol.ERROR_BAD_REQUEST,
    )


def test_ask_with_blank_question_returns_bad_request(monkeypatch):
    async_to_sync(assert_ready_protocol_error)(
        monkeypatch,
        {protocol.FIELD_ACTION: protocol.ACTION_ASK, protocol.FIELD_QUESTION: "   "},
        protocol.ERROR_BAD_REQUEST,
    )


def test_ask_with_non_string_question_returns_bad_request(monkeypatch):
    async_to_sync(assert_ready_protocol_error)(
        monkeypatch,
        {
            protocol.FIELD_ACTION: protocol.ACTION_ASK,
            protocol.FIELD_QUESTION: ["not", "a", "string"],
        },
        protocol.ERROR_BAD_REQUEST,
    )


def test_ask_before_select_returns_no_documents():
    async_to_sync(assert_protocol_error)(ask_message(), protocol.ERROR_NO_DOCUMENTS)


def test_ask_with_missing_question_before_select_returns_no_documents():
    async_to_sync(assert_protocol_error)(
        {protocol.FIELD_ACTION: protocol.ACTION_ASK},
        protocol.ERROR_NO_DOCUMENTS,
    )


async def assert_select_valid_file_ids_transitions_to_ready(monkeypatch):
    async def instant_ingest(self, file_ids):
        return {
            protocol.FIELD_INDEXED_FILES: len(file_ids),
            protocol.FIELD_SKIPPED_FILES: [],
        }

    patch_consumer_hook(monkeypatch, "run_ingest", instant_ingest)
    communicator, connected, _ = await connect_communicator()

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

    await communicator.disconnect()


def test_select_valid_file_ids_transitions_to_ready(monkeypatch):
    async_to_sync(assert_select_valid_file_ids_transitions_to_ready)(monkeypatch)


async def assert_select_while_ingesting_returns_already_selected(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_ingest(self, file_ids):
        started.set()
        await release.wait()
        return {
            protocol.FIELD_INDEXED_FILES: len(file_ids),
            protocol.FIELD_SKIPPED_FILES: [],
        }

    patch_consumer_hook(monkeypatch, "run_ingest", delayed_ingest)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_INGESTING,
    }
    await asyncio.wait_for(started.wait(), timeout=1)

    await communicator.send_json_to(select_message())
    assert_error(
        await communicator.receive_json_from(),
        protocol.ERROR_ALREADY_SELECTED,
    )

    release.set()
    await communicator.receive_json_from()
    await communicator.disconnect()


def test_select_while_ingesting_returns_already_selected(monkeypatch):
    async_to_sync(assert_select_while_ingesting_returns_already_selected)(monkeypatch)


async def assert_invalid_select_while_ingesting_returns_already_selected(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_ingest(self, file_ids):
        started.set()
        await release.wait()
        return {
            protocol.FIELD_INDEXED_FILES: len(file_ids),
            protocol.FIELD_SKIPPED_FILES: [],
        }

    patch_consumer_hook(monkeypatch, "run_ingest", delayed_ingest)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_INGESTING,
    }
    await asyncio.wait_for(started.wait(), timeout=1)

    await communicator.send_json_to(
        {
            protocol.FIELD_ACTION: protocol.ACTION_SELECT,
            protocol.FIELD_FILE_IDS: ["not-a-uuid"],
        }
    )
    assert_error(
        await communicator.receive_json_from(),
        protocol.ERROR_ALREADY_SELECTED,
    )

    release.set()
    await communicator.receive_json_from()
    await communicator.disconnect()


def test_invalid_select_while_ingesting_returns_already_selected(monkeypatch):
    async_to_sync(assert_invalid_select_while_ingesting_returns_already_selected)(
        monkeypatch
    )


async def assert_ask_while_ingesting_returns_not_ready(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_ingest(self, file_ids):
        started.set()
        await release.wait()
        return {
            protocol.FIELD_INDEXED_FILES: len(file_ids),
            protocol.FIELD_SKIPPED_FILES: [],
        }

    patch_consumer_hook(monkeypatch, "run_ingest", delayed_ingest)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_INGESTING,
    }
    await asyncio.wait_for(started.wait(), timeout=1)

    await communicator.send_json_to(ask_message())
    assert_error(await communicator.receive_json_from(), protocol.ERROR_NOT_READY)

    release.set()
    await communicator.receive_json_from()
    await communicator.disconnect()


def test_ask_while_ingesting_returns_not_ready(monkeypatch):
    async_to_sync(assert_ask_while_ingesting_returns_not_ready)(monkeypatch)


async def assert_ingest_exception_resets_state_for_retry(monkeypatch):
    calls = 0
    second_started = asyncio.Event()
    release_second = asyncio.Event()

    async def flaky_ingest(self, file_ids):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated ingest failure")

        second_started.set()
        await release_second.wait()
        return {
            protocol.FIELD_INDEXED_FILES: len(file_ids),
            protocol.FIELD_SKIPPED_FILES: [],
        }

    patch_consumer_hook(monkeypatch, "run_ingest", flaky_ingest)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_INGESTING,
    }
    assert_error(await communicator.receive_json_from(), protocol.ERROR_NO_DOCUMENTS)

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_INGESTING,
    }
    await asyncio.wait_for(second_started.wait(), timeout=1)

    release_second.set()
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_READY,
        protocol.FIELD_INDEXED_FILES: 1,
        protocol.FIELD_SKIPPED_FILES: [],
    }
    assert calls == 2

    await communicator.disconnect()


def test_ingest_exception_resets_state_for_retry(monkeypatch):
    async_to_sync(assert_ingest_exception_resets_state_for_retry)(monkeypatch)


async def assert_no_indexed_documents_resets_state_for_retry(monkeypatch):
    calls = 0

    async def no_documents_then_success(self, file_ids):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                protocol.FIELD_INDEXED_FILES: 0,
                protocol.FIELD_SKIPPED_FILES: [
                    {
                        protocol.FIELD_FILE_ID: file_ids[0],
                        protocol.FIELD_REASON: protocol.SKIP_NOT_FOUND_OR_NOT_OWNED,
                    }
                ],
            }

        return {
            protocol.FIELD_INDEXED_FILES: len(file_ids),
            protocol.FIELD_SKIPPED_FILES: [],
        }

    patch_consumer_hook(monkeypatch, "run_ingest", no_documents_then_success)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_INGESTING,
    }
    assert_error(await communicator.receive_json_from(), protocol.ERROR_NO_DOCUMENTS)
    assert await communicator.receive_nothing(timeout=0.05, interval=0.01)

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
    assert calls == 2

    await communicator.disconnect()


def test_no_indexed_documents_resets_state_for_retry(monkeypatch):
    async_to_sync(assert_no_indexed_documents_resets_state_for_retry)(monkeypatch)


async def assert_select_while_ready_returns_already_selected(monkeypatch):
    communicator = await connect_ready_communicator(monkeypatch)

    await communicator.send_json_to(select_message())
    assert_error(
        await communicator.receive_json_from(),
        protocol.ERROR_ALREADY_SELECTED,
    )

    await communicator.disconnect()


def test_select_while_ready_returns_already_selected(monkeypatch):
    async_to_sync(assert_select_while_ready_returns_already_selected)(monkeypatch)


async def connect_answering_communicator(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_answer(self, question):
        started.set()
        await release.wait()
        yield {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
            protocol.FIELD_DATA: "answer",
        }
        yield {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
            protocol.FIELD_SOURCES: [],
        }

    patch_consumer_hook(monkeypatch, "run_answer", delayed_answer)
    communicator = await connect_ready_communicator(monkeypatch)

    await communicator.send_json_to(ask_message())
    await asyncio.wait_for(started.wait(), timeout=1)

    return communicator, release


async def finish_delayed_answer(communicator, release):
    release.set()
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "answer",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
        protocol.FIELD_SOURCES: [],
    }
    await communicator.disconnect()


async def assert_select_while_answering_returns_already_selected(monkeypatch):
    communicator, release = await connect_answering_communicator(monkeypatch)

    await communicator.send_json_to(select_message())
    assert_error(
        await communicator.receive_json_from(),
        protocol.ERROR_ALREADY_SELECTED,
    )

    await finish_delayed_answer(communicator, release)


def test_select_while_answering_returns_already_selected(monkeypatch):
    async_to_sync(assert_select_while_answering_returns_already_selected)(monkeypatch)


async def assert_ask_while_answering_returns_busy(monkeypatch):
    communicator, release = await connect_answering_communicator(monkeypatch)

    await communicator.send_json_to(ask_message("Second question?"))
    assert_error(await communicator.receive_json_from(), protocol.ERROR_BUSY)

    await finish_delayed_answer(communicator, release)


def test_ask_while_answering_returns_busy(monkeypatch):
    async_to_sync(assert_ask_while_answering_returns_busy)(monkeypatch)


async def assert_malformed_ask_while_answering_returns_busy(monkeypatch):
    communicator, release = await connect_answering_communicator(monkeypatch)

    await communicator.send_json_to({protocol.FIELD_ACTION: protocol.ACTION_ASK})
    assert_error(await communicator.receive_json_from(), protocol.ERROR_BUSY)

    await finish_delayed_answer(communicator, release)


def test_malformed_ask_while_answering_returns_busy(monkeypatch):
    async_to_sync(assert_malformed_ask_while_answering_returns_busy)(monkeypatch)


async def assert_successful_answer_returns_state_to_ready(monkeypatch):
    calls = []

    async def successful_answer(self, question):
        calls.append(question)
        yield {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
            protocol.FIELD_DATA: f"answer-{len(calls)}",
        }
        yield {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
            protocol.FIELD_SOURCES: [],
        }

    patch_consumer_hook(monkeypatch, "run_answer", successful_answer)
    communicator = await connect_ready_communicator(monkeypatch)

    await communicator.send_json_to(ask_message("First question?"))
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "answer-1",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
        protocol.FIELD_SOURCES: [],
    }

    await communicator.send_json_to(ask_message("Second question?"))
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_TOKEN,
        protocol.FIELD_DATA: "answer-2",
    }
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
        protocol.FIELD_SOURCES: [],
    }
    assert calls == ["First question?", "Second question?"]

    await communicator.disconnect()


def test_successful_answer_returns_state_to_ready(monkeypatch):
    async_to_sync(assert_successful_answer_returns_state_to_ready)(monkeypatch)


async def assert_rejected_connect_initializes_cleanup_state(monkeypatch):
    from files.consumers import AskVaultConsumer

    observed_states = []
    original_close = AskVaultConsumer.close

    async def observing_close(self, *args, **kwargs):
        observed_states.append(
            {
                "user_id": self.user_id,
                protocol.FIELD_STATE: self.state,
                "has_tasks": hasattr(self, "_tasks"),
            }
        )
        await original_close(self, *args, **kwargs)

    monkeypatch.setattr(AskVaultConsumer, "close", observing_close)

    communicator, connected, detail = await connect_communicator(ASK_VAULT_PATH)

    assert connected is False
    assert detail == protocol.CLOSE_CODE_MISSING_USER_ID
    assert observed_states == [
        {
            "user_id": None,
            protocol.FIELD_STATE: protocol.STATE_DISCONNECTED,
            "has_tasks": True,
        }
    ]


def test_rejected_connect_initializes_cleanup_state(monkeypatch):
    async_to_sync(assert_rejected_connect_initializes_cleanup_state)(monkeypatch)


async def assert_disconnect_cancels_background_tasks(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_ingest(self, file_ids):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    patch_consumer_hook(monkeypatch, "run_ingest", long_ingest)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
        protocol.FIELD_STATE: protocol.STATE_INGESTING,
    }
    await asyncio.wait_for(started.wait(), timeout=1)

    await communicator.disconnect()

    await asyncio.wait_for(cancelled.wait(), timeout=1)


def test_disconnect_cancels_background_tasks(monkeypatch):
    async_to_sync(assert_disconnect_cancels_background_tasks)(monkeypatch)
