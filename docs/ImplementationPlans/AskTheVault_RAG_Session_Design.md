# Ask the Vault — Session-Scoped RAG over Encrypted Files

## 1. What we are trying to achieve

Add a natural-language Q&A capability on top of Abnormal File Vault: a user opens a
session, names the specific files they want to ask about, and then asks questions
whose answers are grounded *only* in those files. Answers stream back token-by-token.

The design is deliberately **session-scoped and ephemeral**. The plaintext derivatives
needed for retrieval (chunk text + embeddings) exist only in process memory, only for
the lifetime of one WebSocket connection, and are destroyed the moment the connection
closes. This maps onto a forensic "open a case → examine these documents → close the
case" workflow, and keeps the vault's per-user isolation and encryption posture intact.

Decisions locked for this iteration:

- **Transport:** a single bidirectional **WebSocket** per session (no SSE, no REST `ask`).
  One socket carries both the inbound questions and the outbound answer tokens.
- **Document selection:** the client sends an **explicit list of file IDs** at session start.
- **Embeddings:** a **local embedding model** running in-process. No external embedding API.
- **Index:** **Chroma `EphemeralClient`** — purely in-memory, per-session, never written to disk.
- **Answer LLM:** an **external LLM API** (e.g. Anthropic/OpenAI) for the generation step.
- **No rate limiting** on the WebSocket for this iteration.

### Why WebSocket (the one-line rationale)

One WebSocket = one session = one consumer instance = one worker = one in-memory index.
The connection lifetime *is* the session lifetime, so cleanup is uniform and guaranteed:
the `disconnect` handler fires on explicit close, tab close, crash, and network drop
alike. Because the inbound questions travel over the *same* pinned connection, nothing
"floats" to a different worker — the affinity problem that a REST-`ask`-on-separate-POSTs
design would create simply does not arise.

### Two security surfaces, named honestly

These are deliberate, documented trade-offs, not oversights:

- **At-rest surface — closed.** Embeddings and chunk text live only in `EphemeralClient`
  memory for the session, so the AES-at-rest guarantee on the original files is not
  undermined by a persistent plaintext derivative on disk.
- **In-transit egress surface — open and accepted.** The answer step sends decrypted
  chunk text to an **external LLM API**. For a security product this is a real egress of
  forensic content to a third party and would, in production, warrant a data-processing
  agreement, redaction, or a self-hosted model. It is accepted here for the learning
  iteration and called out so the trade is explicit.

---

## 2. Architecture diagram

The REST/WSGI stack (left) is unchanged from the existing submission. The RAG feature
adds a **second serving path** on the right: an ASGI server (uvicorn/daphne) running
Django Channels, holding the WebSocket and the in-memory session index.

```mermaid
graph TD
    Client([Browser / WS Client])

    subgraph Docker Compose
        subgraph Backend Container

            subgraph "WSGI path — existing REST API"
                WSGI[Gunicorn / core/wsgi.py]
                WSGI --> MW[UserIdMiddleware]
                MW --> Throttle[SlidingWindowThrottle]
                Throttle --> ViewSet[FileViewSet thin orchestrator]
                ViewSet --> DS[DeduplicationService]
                ViewSet --> ES1[EncryptionService]
                ViewSet --> QS[FileQueryService]
            end

            subgraph "ASGI path — new RAG feature"
                ASGI[uvicorn / daphne · core/asgi.py]
                ASGI --> CONS[AskVaultConsumer · AsyncWebsocketConsumer]
                CONS --> AUTH[UserId from handshake headers · scope]
                CONS --> ING[Ingest pipeline · sync_to_async]
                ING --> ES2[EncryptionService · decrypt]
                ING --> EMB[Local embedding model · in-process]
                ING --> CHROMA[(Chroma EphemeralClient · in-memory · per session)]
                CONS --> RET[Retriever · MMR + ContextualCompression]
                RET --> CHROMA
                CONS --> LLM[External LLM API · streaming]
            end
        end

        DB[(PostgreSQL 16 · filevault @ postgres:5432)]
        FS[/Docker Volume · /app/media · AES ciphertext/]
    end

    Client -->|HTTP :8000| WSGI
    Client -->|WS :8001| ASGI

    DS --> DB
    QS --> DB
    ViewSet --> DB
    ES1 --> FS
    ES2 --> FS

    style CONS fill:#D5E8F0,stroke:#2E75B6
    style ING fill:#D5E8F0,stroke:#2E75B6
    style RET fill:#D5E8F0,stroke:#2E75B6
    style CHROMA fill:#FFF2CC,stroke:#D6B656
    style LLM fill:#F8CECC,stroke:#B85450
    style DB fill:#F2F2F2,stroke:#888888
    style FS fill:#F2F2F2,stroke:#888888
```

Legend: blue = new consumer/RAG logic, yellow = in-memory ephemeral store,
red = external egress, grey = shared persistent stores. The original `EncryptionService`
is reused by the ingest path (`ES2`) to decrypt files for chunking — no second crypto
implementation.

---

## 3. Data flow

The session moves through a small set of states, all on **one consumer instance, one
worker**. The two states that perform blocking work (ingest, ask) are the only places
that cross the async/sync boundary.

```
connect            → validate UserId from handshake headers; accept socket;
                     create per-session EphemeralClient collection (empty)
                     → state: ready

receive "select"   → { file_ids: [...] }
                     → state: ingesting   (blocking, wrapped in sync_to_async)
                       fetch File records (ORM) → ownership check (must belong to UserId)
                       → read ciphertext from /app/media → EncryptionService.decrypt
                       → chunk → embed (local model) → add to session collection
                     → send "ready"       → state: ready/asking

receive "ask"      → { question: "..." }
                     → state: asking      (retrieval blocking, wrapped in sync_to_async)
                       retriever (MMR + ContextualCompression) over session collection
                       → build prompt with retrieved context
                       → call external LLM API (streaming)
                       → await self.send(token) for each token as it arrives
                     → (loop back to ready for the next question)

disconnect         → delete collection / drop EphemeralClient → memory reclaimed
                     (fires on explicit close, tab close, crash, network drop)
```

### Async/sync boundary — the rule

Channels consumers run on **one event-loop thread shared across every connection on the
worker**. Any unwrapped blocking call freezes that thread and therefore stalls *every*
connection on the worker, not just the caller. So every line that touches the ORM, the
filesystem, the encryption library, the local embedding model, or the retriever runs
through `sync_to_async` / `database_sync_to_async`. The blocking still happens — it just
happens on a threadpool thread, off the shared event loop.

The whole ingest is wrapped as **one** `sync_to_async` unit (one threadpool hop for the
entire fetch → decrypt → chunk → embed → add pipeline), rather than wrapping each call
separately — it is logically one atomic "load these documents" operation.

---

## 4. Sequence diagrams

### 4.1 Session start + ingest

```mermaid
sequenceDiagram
    participant C as WS Client
    participant K as AskVaultConsumer
    participant DB as PostgreSQL
    participant E as EncryptionService
    participant FS as File Storage
    participant M as Local Embedding Model
    participant X as Chroma EphemeralClient

    C->>K: WS upgrade (UserId header)
    K->>K: read UserId from scope headers - validate
    alt missing / invalid UserId
        K-->>C: close 4401
    else valid
        K->>X: create per-session collection (in-memory)
        K-->>C: accept - status ready
    end

    C->>K: action select, file_ids id1 id2
    Note over K,X: ingesting - whole block in sync_to_async (one threadpool hop)
    K->>DB: SELECT * FROM files WHERE id IN (...) AND user_id = UserId
    DB-->>K: owned records only (others silently dropped)
    loop each owned file
        K->>FS: read ciphertext
        FS-->>K: ciphertext bytes
        K->>E: decrypt_file(ciphertext, iv)
        E-->>K: plaintext bytes
        K->>K: chunk plaintext
        K->>M: embed(chunks) - local, in-process
        M-->>K: vectors
        K->>X: add(vectors, text, metadata user_id file_id file_type)
    end
    K-->>C: status ready, indexed_files n
```

### 4.2 Ask + streamed answer

```mermaid
sequenceDiagram
    participant C as WS Client
    participant K as AskVaultConsumer
    participant X as Chroma EphemeralClient
    participant R as Retriever (MMR + Compression)
    participant L as External LLM API

    C->>K: {action: "ask", question: "..."}
    Note over K,R: asking — retrieval in sync_to_async
    K->>R: get_relevant_documents(question)
    R->>X: similarity search (MMR, k, fetch_k)
    X-->>R: candidate chunks (diverse, non-redundant)
    R->>R: ContextualCompression — keep only relevant spans
    R-->>K: compressed context
    K->>K: build prompt(question, context)
    K->>L: stream completion(prompt)
    loop each token
        L-->>K: token
        K-->>C: {type: "token", data: token}
    end
    K-->>C: {type: "done", sources: [file_id, ...]}
```

### 4.3 Session end + cleanup

```mermaid
sequenceDiagram
    participant C as WS Client
    participant K as AskVaultConsumer
    participant X as Chroma EphemeralClient

    alt explicit end / tab close / crash / network drop
        C-->>K: connection closed (any cause)
    end
    Note over K: disconnect() — single guaranteed cleanup hook
    K->>X: delete collection / drop client
    X-->>K: memory reclaimed
    Note over K,X: No disk artifacts to clean — index was never persisted
```

---

## 5. Main implementation details to note

### Serving path

- **Second serving path, not a library.** The WebSocket needs Django Channels under an
  ASGI server (uvicorn/daphne). The existing DRF REST API stays on gunicorn/WSGI,
  unchanged. Both run side by side; this is a real architectural cost, not a footnote.
- `core/asgi.py` gains a `ProtocolTypeRouter` routing `http` to the Django ASGI app and
  `websocket` to a `URLRouter` pointing at `AskVaultConsumer`.

### Auth on the handshake

- The `UserId` header rides on the WebSocket **upgrade request**, so it is available once
  at `connect()` via `self.scope["headers"]` — not through the WSGI `UserIdMiddleware`.
- Validate once at connect, store `user_id` on the consumer instance for the session's
  life. This is connection-oriented auth (validate once per connection) vs. the REST
  model's request-oriented auth (validate every request).

### Ownership enforcement

- The `select` query filters `WHERE id IN (...) AND user_id = <session user>`. File IDs
  the user doesn't own are silently dropped, never indexed. No cross-user content can
  enter the session index — the same per-user isolation the REST API enforces.

### Async/sync discipline

- Every ORM, filesystem, decryption, embedding, and retriever call is wrapped
  (`sync_to_async` / `database_sync_to_async`).
- The ORM is synchronous regardless of the PostgreSQL/`psycopg3` driver — the `a`-prefixed
  ORM methods (`aget`, `aexists`, …) are themselves threadpool wrappers, not a truly
  non-blocking DB path. Either approach keeps the event loop free; what matters is that
  nothing blocking runs unwrapped on the event-loop thread.
- Ingest is wrapped as one unit (one hop), not call-by-call.

### Index lifecycle

- `EphemeralClient` collection is created in `connect()`, populated in `select`, queried
  in `ask`, destroyed in `disconnect()`.
- Because it lives in one worker's process memory, the session is **pinned by affinity**
  to that worker — but that worker is simultaneously holding many *other* users' sessions
  (idle WebSocket connections are nearly free). The pinning guarantees the index is
  reachable for this session's questions; it does not give the session the worker to
  itself.

### Retriever construction

- Base retriever over the session collection with `search_type="mmr"` (Maximum Marginal
  Relevance) so returned chunks are diverse rather than near-duplicates — conceptually
  the retrieval-time analogue of the vault's file-level deduplication.
- Wrapped in a `ContextualCompressionRetriever` so only the relevant spans of each chunk
  reach the prompt, keeping the context sent to the external LLM small and clean.
- Metadata stored alongside each chunk (`user_id`, `file_id`, `file_type`) supports
  metadata filtering at query time and lets the `done` message report which files the
  answer drew from.

### Streaming

- Tokens are pushed with `await self.send(...)` as the external LLM produces them — the
  same socket carries the inbound question and the outbound stream.
- Bridging the LLM's streaming output into async `send` calls is the fiddliest part of
  the implementation (a sync generator → async iteration bridge if the client library is
  sync). Worth prototyping in isolation before wiring into the consumer.

---

## 6. Known gaps / deferred (deliberately out of scope this iteration)

- **No rate limiting on the WebSocket.** The DRF `SlidingWindowThrottle` does not apply to
  WS frames; a per-question limit inside `receive()` is deferred.
- **Re-embedding cost per session.** Embeddings are rebuilt every session start (the price
  of purely-ephemeral indexing). Acceptable for a small, local-model, low-concurrency
  workload; would need revisiting for large document sets or frequent re-opening.
- **Threadpool occupation under concurrency.** Local-model ingest occupies a threadpool
  thread for its duration. Fine at low concurrency; many simultaneous ingests could
  starve the pool. Offloading ingest to Celery is the escalation path — but note it would
  force a *shared* vector store (separate process can't share the in-memory index),
  reintroducing the at-rest surface this design closed. Not worth it at this scale.
- **External-LLM egress.** Decrypted forensic content leaves the box at answer time
  (see §1). Production would need a DPA, redaction, or a self-hosted model.
- **No persistence / resumability.** A dropped connection ends the session and discards
  the index by design; there is no "resume my session" path.
```