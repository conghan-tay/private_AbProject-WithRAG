# Ask the Vault — Session-Scoped RAG over Encrypted TXT Files

## 1. Goal

Add a natural-language Q&A capability on top of Abnormal File Vault. A user opens a
WebSocket session, selects specific vault files, and asks questions whose answers are
grounded in the selected files. Answers stream back token-by-token over the same socket.

This version is deliberately scoped for local learning:

- The session index is ephemeral and lives only for one WebSocket connection.
- v1 indexes only `text/plain` files.
- Embeddings use OpenAI `text-embedding-3-small` through LangChain.
- ChromaDB is used only through `EphemeralClient`; no vector store is persisted.
- The existing REST API, upload flow, encryption format, deduplication behavior, and
  storage model remain unchanged.

The feature is intentionally non-resumable. When the WebSocket closes, the selected
documents, chunks, embeddings, and Chroma collection are discarded.

### Locked Decisions

- **Transport:** one bidirectional WebSocket per RAG session.
- **Runtime:** separate `rag_ws` Docker Compose service on port `8001`, using the same
  backend image and codebase as the REST API.
- **Browser-compatible auth:** WebSocket URL uses `?user_id=<value>`. Browser clients
  cannot set arbitrary WebSocket upgrade headers.
- **Document selection:** client sends an explicit one-shot list of file IDs.
- **Document support:** v1 supports only `text/plain`; other MIME types are skipped.
- **Encryption/decryption:** reuse the current `EncryptionService.decrypt_file_stream`
  and the same reference-to-original resolution used by the download endpoint.
- **RAG framework:** LangChain.
- **Vector database:** ChromaDB `EphemeralClient`.
- **Embedding model:** OpenAI `text-embedding-3-small`, dimensions `1536`.
- **Answer LLM:** external streaming LLM API.
- **State model:** `connected_no_documents -> ingesting -> ready -> answering -> disconnected`.
- **No persistent RAG artifacts:** no chunk text, embeddings, or Chroma data on disk.

### Security and Egress Note

The original vault files remain encrypted at rest. RAG requires plaintext derivatives
during a session: decrypted text, chunks, embeddings, and prompts. These live only in
memory locally, except for accepted external API egress:

- During indexing, plaintext chunks and embedding inputs are sent to OpenAI embeddings.
- During answering, retrieved chunk text and the question are sent to the external LLM.

This is acceptable for the current local learning scope. In production, this would need a
data-processing review, redaction, or self-hosted embedding and generation models.

Grounding is prompt-guided and source-attributed, not a hard guarantee. The `done.sources`
field is deterministic because it is derived from retrieved chunk metadata, but future
claim verification would be required to guarantee every generated claim is supported.

---

## 2. Runtime Architecture

The current REST API remains on the WSGI path. RAG adds a second ASGI path in a separate
Compose service. This is a separate runtime process/container, not a separate codebase and
not a remote File or Encryption microservice.

```mermaid
graph TD
    Client([Browser / WS Client])

    subgraph Docker Compose
        Redis[(Redis)]
        DB[(PostgreSQL 16)]
        FS[/backend_storage volume<br/>/app/media AES ciphertext/]

        subgraph "backend service"
            WSGI[Gunicorn / core.wsgi<br/>:8000]
            REST[DRF FileViewSet]
            REST --> FileModel1[File model]
            REST --> Enc1[EncryptionService]
        end

        subgraph "rag_ws service"
            ASGI[Uvicorn / core.asgi<br/>:8001]
            CONS[AskVaultConsumer]
            ING[TXT ingest pipeline]
            RET[Retriever]
            CHROMA[(Chroma EphemeralClient<br/>per session)]
            EMB[OpenAIEmbeddings<br/>text-embedding-3-small]
            LLM[External streaming LLM]

            ASGI --> CONS
            CONS --> ING
            ING --> FileModel2[File model]
            ING --> Enc2[EncryptionService]
            ING --> EMB
            ING --> CHROMA
            CONS --> RET
            RET --> CHROMA
            CONS --> LLM
        end
    end

    Client -->|HTTP :8000| WSGI
    Client -->|WS :8001<br/>/ws/ask-vault/?user_id=...| ASGI

    REST --> DB
    FileModel1 --> DB
    FileModel2 --> DB
    Enc1 --> FS
    Enc2 --> FS
    WSGI --> Redis
```

### Docker Compose Shape

`backend` keeps running Gunicorn/WSGI:

```yaml
backend:
  build:
    context: ./backend
    dockerfile: Dockerfile
  command: ./start.sh
  ports:
    - "8000:8000"
  volumes:
    - backend_storage:/app/media
    - backend_static:/app/staticfiles
  environment:
    - DATABASE_URL=postgres://filevault:filevault@postgres:5432/filevault
    - REDIS_URL=redis://redis:6379/0
```

`rag_ws` uses the same image and code, but starts Uvicorn/ASGI:

```yaml
rag_ws:
  build:
    context: ./backend
    dockerfile: Dockerfile
  command: uvicorn core.asgi:application --host 0.0.0.0 --port 8001
  ports:
    - "8001:8001"
  volumes:
    - backend_storage:/app/media
  environment:
    - DATABASE_URL=postgres://filevault:filevault@postgres:5432/filevault
    - REDIS_URL=redis://redis:6379/0
    - OPENAI_API_KEY=${OPENAI_API_KEY}
```

`rag_ws` accesses existing services directly:

- `File` model: imported from `files.models`, backed by the same Postgres database.
- `EncryptionService`: imported from `files.services.encryption`.
- Encrypted bytes: read from the shared `/app/media` volume.
- Settings/secrets: loaded through the same Django settings module and environment.

---

## 3. WebSocket Protocol

### Endpoint

```text
ws://localhost:8001/ws/ask-vault/?user_id=<user-id>
```

`user_id` query-param validation:

- Missing `user_id`: close during `connect` with `4401`.
- Blank or whitespace-only `user_id`: close during `connect` with `4400`.
- Valid `user_id`: accept socket, store `self.user_id`, initialize session state.

The REST API still uses the `UserId` HTTP header. The WebSocket path uses query-param auth
because browser WebSocket clients cannot set custom upgrade headers.

### Session State Model

```text
connected_no_documents -> ingesting -> ready -> answering -> ready -> disconnected
```

| Incoming | connected_no_documents | ingesting | ready | answering |
|----------|------------------------|-----------|-------|-----------|
| `select` | ingest | `already_selected` | `already_selected` | `already_selected` |
| `ask` | `no_documents` | `not_ready` | answer | `busy` |

`select` is one-shot. The document set is immutable for the session.

`ask` is single-turn. Conversation history is not threaded into later asks.

### Client to Server

`select`:

```json
{ "action": "select", "file_ids": ["uuid1", "uuid2"] }
```

`ask`:

```json
{ "action": "ask", "question": "What IOCs were listed in the notes?" }
```

Validation:

- `action` must be present and known.
- `file_ids` must be a non-empty array of valid UUID strings.
- `question` must be a non-empty string.
- Malformed JSON or invalid fields produce `error` with `bad_request`.

### Server to Client

Initial accept:

```json
{ "type": "status", "state": "connected_no_documents" }
```

Ingest started:

```json
{ "type": "status", "state": "ingesting" }
```

Ingest complete:

```json
{
  "type": "ready",
  "indexed_files": 2,
  "skipped_files": [
    { "file_id": "uuid3", "reason": "not_found_or_not_owned" },
    { "file_id": "uuid4", "reason": "unsupported_type", "file_type": "application/pdf" }
  ]
}
```

If no requested files are indexed, the terminal selection response is:

```json
{ "type": "error", "code": "no_documents", "message": "No supported owned TXT files were selected." }
```

Token stream:

```json
{ "type": "token", "data": "The " }
```

Grounded answer complete:

```json
{ "type": "done", "sources": ["uuid1"] }
```

No-answer short-circuit:

```json
{ "type": "no_answer", "reason": "not_in_documents" }
```

Error:

```json
{ "type": "error", "code": "not_ready", "message": "Documents are still being indexed." }
```

Error codes:

| Code | Meaning |
|------|---------|
| `bad_request` | Malformed JSON, unknown action, or invalid fields |
| `already_selected` | A second `select` was attempted |
| `no_documents` | `ask` before select, or selection indexed no usable files |
| `not_ready` | `ask` while ingest is still running |
| `busy` | `ask` while a prior answer is streaming |
| `unsupported_encoding` | TXT file could not be decoded as UTF-8 |
| `retrieval_failed` | Chroma or retriever error |
| `llm_failed` | External LLM error |

If `llm_failed` occurs after some tokens streamed, the client must treat the answer as
failed unless a terminal `done` message arrives.

---

## 4. TXT Ingest Pipeline

The ingest path must match the existing download semantics for encrypted originals and
deduplicated references.

### Selection and Ownership

For a `select` payload:

1. Parse and validate UUIDs.
2. Query `File.objects.select_related("original_file").filter(id__in=file_ids, user_id=user_id)`.
3. Any requested ID not returned is skipped with `not_found_or_not_owned`.
4. Any returned row whose `file_type != "text/plain"` is skipped with `unsupported_type`.

No cross-user file is ever indexed. Skipped unowned IDs are not distinguished from missing
IDs to avoid leaking existence.

### Reference-Safe Storage Resolution

For each owned TXT record:

```python
storage_record = record.original_file if record.is_reference else record
if storage_record is None or not storage_record.file:
    skip malformed_storage
```

The selected logical `record.id` remains the source ID stored in chunk metadata. The
physical encrypted bytes and IV come from `storage_record`.

This is required because reference rows have no file and no IV. They point at the original
or promoted original's encrypted storage object.

### Decryption

Use the current chunked AES-GCM stream:

```python
storage_record.file.open("rb")
try:
    plaintext = b"".join(
        EncryptionService.decrypt_file_stream(
            storage_record.file,
            storage_record.encryption_iv,
            storage_record.size,
        )
    )
finally:
    storage_record.file.close()
```

This preserves the current encrypted file format:

- 12-byte base nonce stored in `encryption_iv`.
- AES-GCM per chunk.
- 16-byte tag per encrypted chunk.
- Original plaintext size from `storage_record.size`.

### Decode and Split

Decode as UTF-8:

```python
text = plaintext.decode("utf-8")
```

If decoding fails, reject selection with `unsupported_encoding` for the file. For v1,
do not silently replace invalid bytes because forensic text should not be mutated without
being explicit.

Split with LangChain:

```python
RecursiveCharacterTextSplitter(
    chunk_size=settings.RAG_CHUNK_SIZE,
    chunk_overlap=settings.RAG_CHUNK_OVERLAP,
)
```

Default settings:

```python
RAG_CHUNK_SIZE = 1000
RAG_CHUNK_OVERLAP = 150
```

Chunk metadata:

```json
{
  "user_id": "user123",
  "file_id": "selected-logical-file-id",
  "storage_file_id": "original-or-promoted-original-id",
  "original_filename": "notes.txt",
  "file_type": "text/plain",
  "chunk_index": 0
}
```

---

## 5. LangChain, Chroma, and Embeddings

### Dependencies

Pin the RAG dependencies in `backend/requirements.txt`:

```txt
channels==4.3.2
uvicorn==0.38.0
langchain==1.3.9
langchain-openai==1.3.2
langchain-chroma==1.1.0
langchain-text-splitters==1.1.2
chromadb==1.5.9
```

The existing repo is Python 3.10-based, so only versions supporting Python 3.10 should be
accepted.

### Settings

Add RAG settings in `backend/core/settings.py`:

```python
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
RAG_EMBEDDING_MODEL = os.environ.get("RAG_EMBEDDING_MODEL", "text-embedding-3-small")
RAG_EMBEDDING_DIMENSIONS = int(os.environ.get("RAG_EMBEDDING_DIMENSIONS", 1536))
RAG_CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", 1000))
RAG_CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", 150))
RAG_RETRIEVAL_K = int(os.environ.get("RAG_RETRIEVAL_K", 4))
RAG_RETRIEVAL_FETCH_K = int(os.environ.get("RAG_RETRIEVAL_FETCH_K", 12))
RAG_MAX_DISTANCE = float(os.environ.get("RAG_MAX_DISTANCE", 0.35))
RAG_LLM_MODEL = os.environ.get("RAG_LLM_MODEL", "gpt-4.1-mini")
```

`RAG_MAX_DISTANCE` is named as distance, not relevance floor, because Chroma/LangChain
score APIs can return distances where lower is more similar. The implementation must
verify score direction in tests before relying on the threshold.

### Vector Store Initialization

Create one Chroma client and collection per WebSocket session:

```python
import chromadb
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

client = chromadb.EphemeralClient()

embeddings = OpenAIEmbeddings(
    model=settings.RAG_EMBEDDING_MODEL,
    dimensions=settings.RAG_EMBEDDING_DIMENSIONS,
)

vector_store = Chroma(
    client=client,
    collection_name=f"askvault-{session_id}",
    embedding_function=embeddings,
    collection_configuration={"hnsw": {"space": "cosine"}},
)
```

No `persist_directory` may be passed anywhere. This is a hard design invariant and should
be tested.

### Index Lifecycle

- `connect`: initialize session state; Chroma objects may be created lazily on `select`.
- `select`: create collection, split TXT, embed chunks, add documents and metadata.
- `ask`: query the collection.
- `disconnect`: delete collection and drop Python references.

Cleanup:

```python
try:
    vector_store.delete_collection()
except Exception:
    pass
finally:
    vector_store = None
    chroma_client = None
```

---

## 6. Retrieval, Prompting, and Streaming

### Retrieval

Use MMR retrieval for diverse chunks:

```python
retriever = vector_store.as_retriever(
    search_type="mmr",
    search_kwargs={
        "k": settings.RAG_RETRIEVAL_K,
        "fetch_k": settings.RAG_RETRIEVAL_FETCH_K,
    },
)
```

For thresholding, use `similarity_search_with_score` or the equivalent low-level call that
returns scores. Tests must establish whether the score is cosine distance or similarity for
the configured collection.

If the top result is farther than `RAG_MAX_DISTANCE`, return:

```json
{ "type": "no_answer", "reason": "not_in_documents" }
```

The LLM must not be called on `no_answer`.

### Prompt

System prompt:

```text
You are a retrieval-grounded assistant for a secure forensic file vault.
Answer using ONLY the provided context excerpts.

Rules:
- If the context does not contain enough information to answer, say exactly that.
- Do not use outside knowledge to fill gaps.
- Do not speculate or infer beyond what the excerpts state.
- If excerpts conflict, surface the conflict rather than resolving it silently.
- Be concise and precise.
```

User prompt:

```text
Context excerpts:
---
{chunk_text}
(source: {file_id})
---

Question: {question}
```

Sources are deterministic:

```python
sources = sorted({doc.metadata["file_id"] for doc in retrieved_docs})
```

The model does not generate the source list.

### Streaming

During a grounded answer:

1. Move state `ready -> answering`.
2. Retrieve chunks.
3. If threshold fails, emit `no_answer`, move back to `ready`.
4. If threshold passes, stream LLM tokens as `{ "type": "token", "data": "..." }`.
5. Emit `{ "type": "done", "sources": [...] }`.
6. Move state back to `ready`.

If the external LLM fails after partial tokens:

```json
{ "type": "error", "code": "llm_failed", "message": "Answer generation failed." }
```

No `done` is sent after failure.

---

## 7. Test Strategy

Follow the same layered testing philosophy as the main File Vault implementation: write
tests before implementation for each layer, and keep LLM/embedding calls mocked in the
normal test suite.

### 7.1 Test Layers

| Layer | Location | Tool | What it covers |
|-------|----------|------|----------------|
| Unit | `backend/files/tests/test_rag_*.py` | pytest / Django TestCase | Protocol validation, state machine, TXT decode, chunking, reference storage resolution, prompt/source assembly |
| Integration | `backend/files/tests/test_rag_ws.py` | Channels `WebsocketCommunicator` | Connect/auth, select/ask protocol, error codes, ready/no-answer/done messages |
| RAG mocked | `backend/files/tests/test_rag_retrieval.py` | mocks/fakes | Embeddings, vector store behavior, score thresholding, LLM streaming |
| Ephemerality | `backend/files/tests/test_rag_chroma.py` | temp dirs + Chroma | No `persist_directory`, no local Chroma files, collection deleted on disconnect |
| E2E smoke | `tests/e2e/test_rag_ws.py` | requests + websocket client | Upload TXT over REST, select over WS, ask, receive tokens and sources |

### 7.2 Protocol and State Tests

- Connect without `user_id` closes with `4401`.
- Connect with blank `user_id` closes with `4400`.
- Connect with valid `user_id` accepts and sends `connected_no_documents`.
- Malformed JSON returns `bad_request`.
- Unknown action returns `bad_request`.
- `ask` before `select` returns `no_documents`.
- `select` while `ingesting`, `ready`, or `answering` returns `already_selected`.
- `ask` while `ingesting` returns `not_ready`.
- `ask` while `answering` returns `busy`.
- Successful answer returns state to `ready`.

### 7.3 Ingest Tests

- Owned TXT file is decrypted, decoded, split, and indexed.
- File owned by another user is skipped as `not_found_or_not_owned`.
- Missing file ID is skipped as `not_found_or_not_owned`.
- PDF/PNG/MP4 files are skipped as `unsupported_type`.
- Invalid UTF-8 TXT yields `unsupported_encoding`.
- Reference row resolves to its original storage record and decrypts successfully.
- Promoted reference that became an original decrypts successfully.
- If all requested files are skipped, server returns `no_documents`.

### 7.4 Chroma and Embedding Tests

- `chromadb.EphemeralClient()` is used.
- No `persist_directory` is passed to LangChain `Chroma`.
- Collection uses cosine space.
- Documents include stable IDs and metadata.
- A temp directory remains free of Chroma database files after indexing.
- `delete_collection()` runs on disconnect.
- A fresh `EphemeralClient` cannot see old session collections.
- OpenAI embedding object is configured with `text-embedding-3-small` and `1536`.

### 7.5 Retrieval and LLM Tests

- Score-direction test confirms whether lower or higher scores are better for the chosen
  Chroma/LangChain call.
- Top score beyond `RAG_MAX_DISTANCE` emits `no_answer`.
- `no_answer` path never calls the LLM.
- Retrieved chunk metadata produces deterministic sorted `sources`.
- Token streaming emits one `token` message per generated token.
- Successful stream terminates with `done`.
- LLM error after partial tokens emits `llm_failed` and no `done`.

### 7.6 E2E Smoke

Scenario:

1. Start `docker compose up --build`.
2. Upload a TXT file through `POST /api/files/`.
3. Connect to `ws://localhost:8001/ws/ask-vault/?user_id=<same-user>`.
4. Send `select` with the uploaded file ID.
5. Wait for `ready`.
6. Send a grounded `ask`.
7. Assert at least one `token` and terminal `done`.
8. Assert `done.sources` contains the selected file ID.

This test should be gated behind an env var or use mocked LLM behavior so the default test
suite is deterministic and does not spend API credits.

---

## 8. TDD Build Plan

Build in layers. Each step starts with focused failing tests, then the minimum
implementation needed to pass them.

| Step | Tag | What to Build | Tests First | Done When |
|------|-----|---------------|-------------|-----------|
| 1 | KEY | Protocol/state test skeleton | WS URL auth, close codes, JSON validation, state transition tests | Tests fail for missing consumer/routing |
| 2 | KEY | ASGI/Channels skeleton | Same protocol/state tests | Minimal `AskVaultConsumer` accepts valid connections and passes state/protocol tests |
| 3 | KEY | Runtime wiring | Compose/manual smoke for `rag_ws :8001` | `backend` serves REST on `8000`; `rag_ws` serves WS on `8001` |
| 4 | KEY | Ingest unit tests | Owned lookup, cross-user skip, TXT-only skip, invalid UTF-8, reference storage resolution | Tests fail for missing ingest service |
| 5 | KEY | TXT ingest implementation | Ingest tests from Step 4 | Owned TXT files decrypt, decode, split, and report `ready`/`skipped_files` correctly |
| 6 | KEY | Chroma/embedding tests | Mock embedding config, metadata, cosine config, no `persist_directory`, cleanup | Tests fail for missing vector index |
| 7 | KEY | Ephemeral index implementation | Chroma/embedding tests from Step 6 | Per-session Chroma collection is created, populated, and deleted without disk persistence |
| 8 | KEY | Retrieval tests | Score direction, `RAG_MAX_DISTANCE`, `no_answer`, deterministic sources, MMR kwargs | Tests fail for missing retrieval |
| 9 | KEY | Retrieval implementation | Retrieval tests from Step 8 | Relevant questions return sources; off-topic questions return `no_answer` without LLM call |
| 10 | KEY | Streaming tests | Token messages, terminal `done`, `llm_failed`, state reset to `ready` | Tests fail for missing LLM stream bridge |
| 11 | KEY | LLM streaming implementation | Streaming tests from Step 10 | Answer tokens stream over WS and terminate with `done` or `llm_failed` |
| 12 | KEY | Docker E2E smoke | Upload TXT, select, ask, receive tokens/sources | End-to-end local RAG path works with test-gated LLM behavior |
| 13 | KEY | Docs and diagrams | README/Mermaid review | Setup, protocol, config, egress note, TXT-only limitation, and known gaps are documented |

---

## 9. Known Deferred Work

- Support PDF, DOCX, images, audio, and video extraction.
- Add persistent/reusable embeddings for larger or repeated sessions.
- Add production-grade WS rate limiting, per-user active-session limits, and cost controls.
- Add redaction before embedding and LLM calls.
- Add self-hosted embedding and generation options.
- Add post-generation claim verification for stronger grounding guarantees.
- Add resumable sessions.

---

## 10. Assumptions

- This is a local learning feature, not a production abuse-resistant deployment.
- Users are expected to select TXT files, but the backend still enforces `text/plain`.
- OpenAI embeddings are accepted, so plaintext chunks and questions leave the local
  machine during retrieval.
- The external answer LLM is accepted for v1.
- RAG sessions are ephemeral and non-resumable by design.
- Existing file upload, deduplication, quota, encryption, download, and delete behavior
  must remain unchanged.
