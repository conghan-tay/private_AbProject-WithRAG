"""Tests-first contract for the future AskVaultConsumer.

These tests are intentionally red until Step 2 wires Channels routing and adds
files.consumers.AskVaultConsumer with patchable run_ingest/run_answer hooks.
"""

import asyncio
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator

from core.asgi import application


ASK_VAULT_PATH = "/ws/ask-vault/"


def ws_path(user_id="rag-user"):
    return f"{ASK_VAULT_PATH}?user_id={user_id}"


def select_message(file_id=None):
    return {"action": "select", "file_ids": [str(file_id or uuid4())]}


def ask_message(question="What is in the file?"):
    return {"action": "ask", "question": question}


def assert_error(message, code):
    assert message["type"] == "error"
    assert message["code"] == code


def patch_consumer_hook(monkeypatch, name, replacement):
    from files.consumers import AskVaultConsumer

    monkeypatch.setattr(AskVaultConsumer, name, replacement)


async def connect_communicator(path=ws_path()):
    communicator = WebsocketCommunicator(application, path)
    connected, detail = await communicator.connect()
    return communicator, connected, detail


async def connect_ready_communicator(monkeypatch):
    async def instant_ingest(self, file_ids):
        return {"indexed_files": len(file_ids), "skipped_files": []}

    patch_consumer_hook(monkeypatch, "run_ingest", instant_ingest)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    assert await communicator.receive_json_from() == {
        "type": "status",
        "state": "connected_no_documents",
    }

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        "type": "status",
        "state": "ingesting",
    }
    assert await communicator.receive_json_from() == {
        "type": "ready",
        "indexed_files": 1,
        "skipped_files": [],
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
        "type": "status",
        "state": "connected_no_documents",
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
    async_to_sync(assert_disconnects_with_code)(ASK_VAULT_PATH, 4401)


def test_blank_user_id_closes_with_4400():
    async_to_sync(assert_disconnects_with_code)(ws_path("   "), 4400)


def test_valid_user_id_accepts_and_sends_initial_state():
    async_to_sync(assert_connected_initial_status)()


def test_malformed_json_returns_bad_request():
    async_to_sync(assert_protocol_error)("{", "bad_request")


def test_unknown_action_returns_bad_request():
    async_to_sync(assert_protocol_error)({"action": "dance"}, "bad_request")


def test_select_with_missing_file_ids_returns_bad_request():
    async_to_sync(assert_protocol_error)({"action": "select"}, "bad_request")


def test_select_with_empty_file_ids_returns_bad_request():
    async_to_sync(assert_protocol_error)(
        {"action": "select", "file_ids": []},
        "bad_request",
    )


def test_select_with_non_list_file_ids_returns_bad_request():
    async_to_sync(assert_protocol_error)(
        {"action": "select", "file_ids": str(uuid4())},
        "bad_request",
    )


def test_select_with_invalid_uuid_file_id_returns_bad_request():
    async_to_sync(assert_protocol_error)(
        {"action": "select", "file_ids": ["not-a-uuid"]},
        "bad_request",
    )


def test_ask_with_missing_question_returns_bad_request(monkeypatch):
    async_to_sync(assert_ready_protocol_error)(
        monkeypatch,
        {"action": "ask"},
        "bad_request",
    )


def test_ask_with_blank_question_returns_bad_request(monkeypatch):
    async_to_sync(assert_ready_protocol_error)(
        monkeypatch,
        {"action": "ask", "question": "   "},
        "bad_request",
    )


def test_ask_with_non_string_question_returns_bad_request(monkeypatch):
    async_to_sync(assert_ready_protocol_error)(
        monkeypatch,
        {"action": "ask", "question": ["not", "a", "string"]},
        "bad_request",
    )


def test_ask_before_select_returns_no_documents():
    async_to_sync(assert_protocol_error)(ask_message(), "no_documents")


def test_ask_with_missing_question_before_select_returns_no_documents():
    async_to_sync(assert_protocol_error)({"action": "ask"}, "no_documents")


async def assert_select_while_ingesting_returns_already_selected(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_ingest(self, file_ids):
        started.set()
        await release.wait()
        return {"indexed_files": len(file_ids), "skipped_files": []}

    patch_consumer_hook(monkeypatch, "run_ingest", delayed_ingest)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        "type": "status",
        "state": "ingesting",
    }
    await asyncio.wait_for(started.wait(), timeout=1)

    await communicator.send_json_to(select_message())
    assert_error(await communicator.receive_json_from(), "already_selected")

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
        return {"indexed_files": len(file_ids), "skipped_files": []}

    patch_consumer_hook(monkeypatch, "run_ingest", delayed_ingest)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        "type": "status",
        "state": "ingesting",
    }
    await asyncio.wait_for(started.wait(), timeout=1)

    await communicator.send_json_to({"action": "select", "file_ids": ["not-a-uuid"]})
    assert_error(await communicator.receive_json_from(), "already_selected")

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
        return {"indexed_files": len(file_ids), "skipped_files": []}

    patch_consumer_hook(monkeypatch, "run_ingest", delayed_ingest)
    communicator, connected, _ = await connect_communicator()
    assert connected is True
    await communicator.receive_json_from()

    await communicator.send_json_to(select_message())
    assert await communicator.receive_json_from() == {
        "type": "status",
        "state": "ingesting",
    }
    await asyncio.wait_for(started.wait(), timeout=1)

    await communicator.send_json_to(ask_message())
    assert_error(await communicator.receive_json_from(), "not_ready")

    release.set()
    await communicator.receive_json_from()
    await communicator.disconnect()


def test_ask_while_ingesting_returns_not_ready(monkeypatch):
    async_to_sync(assert_ask_while_ingesting_returns_not_ready)(monkeypatch)


async def assert_select_while_ready_returns_already_selected(monkeypatch):
    communicator = await connect_ready_communicator(monkeypatch)

    await communicator.send_json_to(select_message())
    assert_error(await communicator.receive_json_from(), "already_selected")

    await communicator.disconnect()


def test_select_while_ready_returns_already_selected(monkeypatch):
    async_to_sync(assert_select_while_ready_returns_already_selected)(monkeypatch)


async def connect_answering_communicator(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_answer(self, question):
        started.set()
        await release.wait()
        yield {"type": "token", "data": "answer"}
        yield {"type": "done", "sources": []}

    patch_consumer_hook(monkeypatch, "run_answer", delayed_answer)
    communicator = await connect_ready_communicator(monkeypatch)

    await communicator.send_json_to(ask_message())
    await asyncio.wait_for(started.wait(), timeout=1)

    return communicator, release


async def finish_delayed_answer(communicator, release):
    release.set()
    assert await communicator.receive_json_from() == {"type": "token", "data": "answer"}
    assert await communicator.receive_json_from() == {"type": "done", "sources": []}
    await communicator.disconnect()


async def assert_select_while_answering_returns_already_selected(monkeypatch):
    communicator, release = await connect_answering_communicator(monkeypatch)

    await communicator.send_json_to(select_message())
    assert_error(await communicator.receive_json_from(), "already_selected")

    await finish_delayed_answer(communicator, release)


def test_select_while_answering_returns_already_selected(monkeypatch):
    async_to_sync(assert_select_while_answering_returns_already_selected)(monkeypatch)


async def assert_ask_while_answering_returns_busy(monkeypatch):
    communicator, release = await connect_answering_communicator(monkeypatch)

    await communicator.send_json_to(ask_message("Second question?"))
    assert_error(await communicator.receive_json_from(), "busy")

    await finish_delayed_answer(communicator, release)


def test_ask_while_answering_returns_busy(monkeypatch):
    async_to_sync(assert_ask_while_answering_returns_busy)(monkeypatch)


async def assert_malformed_ask_while_answering_returns_busy(monkeypatch):
    communicator, release = await connect_answering_communicator(monkeypatch)

    await communicator.send_json_to({"action": "ask"})
    assert_error(await communicator.receive_json_from(), "busy")

    await finish_delayed_answer(communicator, release)


def test_malformed_ask_while_answering_returns_busy(monkeypatch):
    async_to_sync(assert_malformed_ask_while_answering_returns_busy)(monkeypatch)


async def assert_successful_answer_returns_state_to_ready(monkeypatch):
    calls = []

    async def successful_answer(self, question):
        calls.append(question)
        yield {"type": "token", "data": f"answer-{len(calls)}"}
        yield {"type": "done", "sources": []}

    patch_consumer_hook(monkeypatch, "run_answer", successful_answer)
    communicator = await connect_ready_communicator(monkeypatch)

    await communicator.send_json_to(ask_message("First question?"))
    assert await communicator.receive_json_from() == {"type": "token", "data": "answer-1"}
    assert await communicator.receive_json_from() == {"type": "done", "sources": []}

    await communicator.send_json_to(ask_message("Second question?"))
    assert await communicator.receive_json_from() == {"type": "token", "data": "answer-2"}
    assert await communicator.receive_json_from() == {"type": "done", "sources": []}
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
                "state": self.state,
                "has_tasks": hasattr(self, "_tasks"),
            }
        )
        await original_close(self, *args, **kwargs)

    monkeypatch.setattr(AskVaultConsumer, "close", observing_close)

    communicator, connected, detail = await connect_communicator(ASK_VAULT_PATH)

    assert connected is False
    assert detail == 4401
    assert observed_states == [
        {"user_id": None, "state": "disconnected", "has_tasks": True}
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
        "type": "status",
        "state": "ingesting",
    }
    await asyncio.wait_for(started.wait(), timeout=1)

    await communicator.disconnect()

    await asyncio.wait_for(cancelled.wait(), timeout=1)


def test_disconnect_cancels_background_tasks(monkeypatch):
    async_to_sync(assert_disconnect_cancels_background_tasks)(monkeypatch)
