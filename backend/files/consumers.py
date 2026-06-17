import asyncio
import json
from json import JSONDecodeError
from urllib.parse import parse_qs
from uuid import UUID

from channels.generic.websocket import AsyncWebsocketConsumer


class AskVaultConsumer(AsyncWebsocketConsumer):
    STATE_CONNECTED_NO_DOCUMENTS = "connected_no_documents"
    STATE_INGESTING = "ingesting"
    STATE_READY = "ready"
    STATE_ANSWERING = "answering"
    STATE_DISCONNECTED = "disconnected"

    async def connect(self):
        query_params = parse_qs(
            self.scope.get("query_string", b"").decode("utf-8"),
            keep_blank_values=True,
        )
        user_ids = query_params.get("user_id")

        if user_ids is None:
            await self.close(code=4401)
            return

        user_id = user_ids[0].strip()
        if not user_id:
            await self.close(code=4400)
            return

        self.user_id = user_id
        self.state = self.STATE_CONNECTED_NO_DOCUMENTS
        self.background_task = None

        await self.accept()
        await self.send_json({"type": "status", "state": self.state})

    async def disconnect(self, close_code):
        self.state = self.STATE_DISCONNECTED
        task = getattr(self, "background_task", None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def receive(self, text_data=None, bytes_data=None):
        try:
            payload = json.loads(text_data or "")
        except (JSONDecodeError, TypeError):
            await self.send_error("bad_request")
            return

        if not isinstance(payload, dict):
            await self.send_error("bad_request")
            return

        action = payload.get("action")
        if action == "select":
            await self.handle_select(payload)
        elif action == "ask":
            await self.handle_ask(payload)
        else:
            await self.send_error("bad_request")

    async def handle_select(self, payload):
        file_ids = self.validate_file_ids(payload.get("file_ids"))
        if file_ids is None:
            await self.send_error("bad_request")
            return

        if self.state != self.STATE_CONNECTED_NO_DOCUMENTS:
            await self.send_error("already_selected")
            return

        self.state = self.STATE_INGESTING
        await self.send_json({"type": "status", "state": self.state})
        self.background_task = asyncio.create_task(self.complete_ingest(file_ids))

    async def handle_ask(self, payload):
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            await self.send_error("bad_request")
            return

        if self.state == self.STATE_CONNECTED_NO_DOCUMENTS:
            await self.send_error("no_documents")
            return
        if self.state == self.STATE_INGESTING:
            await self.send_error("not_ready")
            return
        if self.state == self.STATE_ANSWERING:
            await self.send_error("busy")
            return

        self.state = self.STATE_ANSWERING
        self.background_task = asyncio.create_task(self.complete_answer(question.strip()))

    async def complete_ingest(self, file_ids):
        try:
            result = await self.run_ingest(file_ids)
            if self.state == self.STATE_DISCONNECTED:
                return

            self.state = self.STATE_READY
            await self.send_json(
                {
                    "type": "ready",
                    "indexed_files": result.get("indexed_files", 0),
                    "skipped_files": result.get("skipped_files", []),
                }
            )
        except asyncio.CancelledError:
            raise

    async def complete_answer(self, question):
        try:
            async for message in self.run_answer(question):
                if self.state == self.STATE_DISCONNECTED:
                    return
                await self.send_json(message)

            if self.state != self.STATE_DISCONNECTED:
                self.state = self.STATE_READY
        except asyncio.CancelledError:
            raise

    async def run_ingest(self, file_ids):
        return {"indexed_files": 0, "skipped_files": []}

    async def run_answer(self, question):
        yield {"type": "done", "sources": []}

    async def send_json(self, payload):
        await self.send(text_data=json.dumps(payload))

    async def send_error(self, code):
        await self.send_json({"type": "error", "code": code})

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
