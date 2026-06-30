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
