import asyncio
import json
from json import JSONDecodeError
from urllib.parse import parse_qs
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from files import rag_protocol as protocol
from files.services.rag_ingest import TxtIngestService


class AskVaultConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user_id = None
        self.state = protocol.STATE_DISCONNECTED
        self._tasks = set()
        self.ingested_chunks = []

        query_params = parse_qs(
            self.scope.get("query_string", b"").decode("utf-8"),
            keep_blank_values=True,
        )
        user_ids = query_params.get("user_id")

        if user_ids is None:
            await self.close(code=protocol.CLOSE_CODE_MISSING_USER_ID)
            return

        user_id = user_ids[0].strip()
        if not user_id:
            await self.close(code=protocol.CLOSE_CODE_BLANK_USER_ID)
            return

        self.user_id = user_id
        self.state = protocol.STATE_CONNECTED_NO_DOCUMENTS

        await self.accept()
        await self.send_json(
            {
                protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
                protocol.FIELD_STATE: self.state,
            }
        )

    async def disconnect(self, close_code):
        self.state = protocol.STATE_DISCONNECTED
        tasks = list(getattr(self, "_tasks", ()))

        for task in tasks:
            if not task.done():
                task.cancel()

        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def receive(self, text_data=None, bytes_data=None):
        try:
            payload = json.loads(text_data or "")
        except (JSONDecodeError, TypeError):
            await self.send_error(protocol.ERROR_BAD_REQUEST)
            return

        if not isinstance(payload, dict):
            await self.send_error(protocol.ERROR_BAD_REQUEST)
            return

        action = payload.get(protocol.FIELD_ACTION)
        if action == protocol.ACTION_SELECT:
            await self.handle_select(payload)
        elif action == protocol.ACTION_ASK:
            await self.handle_ask(payload)
        else:
            await self.send_error(protocol.ERROR_BAD_REQUEST)

    async def handle_select(self, payload):
        if self.state != protocol.STATE_CONNECTED_NO_DOCUMENTS:
            await self.send_error(protocol.ERROR_ALREADY_SELECTED)
            return

        file_ids = self.validate_file_ids(payload.get(protocol.FIELD_FILE_IDS))
        if file_ids is None:
            await self.send_error(protocol.ERROR_BAD_REQUEST)
            return

        self.state = protocol.STATE_INGESTING
        await self.send_json(
            {
                protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_STATUS,
                protocol.FIELD_STATE: self.state,
            }
        )
        self._spawn(self.complete_ingest(file_ids))

    async def handle_ask(self, payload):
        if self.state == protocol.STATE_CONNECTED_NO_DOCUMENTS:
            await self.send_error(protocol.ERROR_NO_DOCUMENTS)
            return
        if self.state == protocol.STATE_INGESTING:
            await self.send_error(protocol.ERROR_NOT_READY)
            return
        if self.state == protocol.STATE_ANSWERING:
            await self.send_error(protocol.ERROR_BUSY)
            return

        question = payload.get(protocol.FIELD_QUESTION)
        if not isinstance(question, str) or not question.strip():
            await self.send_error(protocol.ERROR_BAD_REQUEST)
            return

        self.state = protocol.STATE_ANSWERING
        self._spawn(self.complete_answer(question.strip()))

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def complete_ingest(self, file_ids):
        try:
            result = await self.run_ingest(file_ids)
            if self.state == protocol.STATE_DISCONNECTED:
                return

            self.state = protocol.STATE_READY
            await self.send_json(
                {
                    protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_READY,
                    protocol.FIELD_INDEXED_FILES: result.get(
                        protocol.FIELD_INDEXED_FILES, 0
                    ),
                    protocol.FIELD_SKIPPED_FILES: result.get(
                        protocol.FIELD_SKIPPED_FILES, []
                    ),
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.state != protocol.STATE_DISCONNECTED:
                self.state = protocol.STATE_CONNECTED_NO_DOCUMENTS
                self.ingested_chunks = []
                await self.send_error(protocol.ERROR_NO_DOCUMENTS)

    async def complete_answer(self, question):
        try:
            async for message in self.run_answer(question):
                if self.state == protocol.STATE_DISCONNECTED:
                    return
                await self.send_json(message)

            if self.state != protocol.STATE_DISCONNECTED:
                self.state = protocol.STATE_READY
        except asyncio.CancelledError:
            raise

    async def run_ingest(self, file_ids):
        result = await sync_to_async(
            TxtIngestService().ingest_files,
            thread_sensitive=True,
        )(self.user_id, file_ids)
        self.ingested_chunks = result.get(protocol.FIELD_CHUNKS, [])
        return result

    async def run_answer(self, question):
        yield {
            protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_DONE,
            protocol.FIELD_SOURCES: [],
        }

    async def send_json(self, payload):
        await self.send(text_data=json.dumps(payload))

    async def send_error(self, code):
        await self.send_json(
            {
                protocol.FIELD_TYPE: protocol.MESSAGE_TYPE_ERROR,
                protocol.FIELD_CODE: code,
            }
        )

    def validate_file_ids(self, file_ids):
        if not isinstance(file_ids, list) or not file_ids:
            return None

        valid_file_ids = []
        for file_id in file_ids:
            if not isinstance(file_id, str):
                return None
            try:
                UUID(file_id)
            except ValueError:
                return None
            valid_file_ids.append(file_id)

        return valid_file_ids
