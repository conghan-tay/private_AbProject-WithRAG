# Architecture Diagram
```mermaid
graph TD
    Client([HTTP Client / E2E Tests]) -->|HTTP :8000| WSGI


    subgraph Docker Compose
    Redis[(Redis\nLua + sorted sets)]

    subgraph Backend Container
        WSGI[Gunicorn / core/wsgi.py]
        WSGI --> MW[UserIdMiddleware\nExtract UserId header]
        MW -->|401/400| ClientErr1([Client Error])
        MW --> Throttle[SlidingWindowThrottle\nRedis Lua/ZSET or LocMem fallback]
        Throttle -->|429 Call Limit Reached| ClientErr2([Client Error])
        Throttle --> ViewSet[FileViewSet\nThin Orchestrator]


        ViewSet --> DS[DeduplicationService\ncompute_hash / find_duplicate\ncreate_reference / promote]
        ViewSet --> ES[EncryptionService\nencrypt_file / decrypt_file]
        ViewSet --> QS[FileQueryService\nbuild_queryset / check_quota\nstorage_stats / file_types]


        DS --> DB[(PostgreSQL 16\nfilevault @ postgres:5432)]
        ES --> FS[/Docker Volume\n/app/media/]
        QS --> DB
        ViewSet --> DB
        Throttle --> Redis
    end
    end


    style DS fill:#D5E8F0,stroke:#2E75B6
    style ES fill:#D5E8F0,stroke:#2E75B6
    style QS fill:#D5E8F0,stroke:#2E75B6
    style Redis fill:#F2F2F2,stroke:#888888
    style DB fill:#F2F2F2,stroke:#888888
    style FS fill:#F2F2F2,stroke:#888888
```

# Event Sequence Diagrams

## Upload — New File

```mermaid
sequenceDiagram
    participant C as Client
    participant MW as UserIdMiddleware
    participant TH as SlidingWindowThrottle
    participant V as FileViewSet
    participant D as DeduplicationService
    participant Q as FileQueryService
    participant E as EncryptionService
    participant DB as SQLite
    participant FS as File Storage


    C->>MW: POST /api/files/ (UserId: user123)
    MW->>TH: pass (UserId attached)
    TH->>V: pass (rate ok)
    V->>D: compute_hash(file_obj)
    D-->>V: sha256_hex
    V->>D: find_duplicate(user123, sha256_hex)
    D->>DB: SELECT WHERE user_id=user123 AND file_hash=sha256_hex
    DB-->>D: None
    D-->>V: None (no duplicate)
    V->>Q: check_quota(user123, file_size)
    Q->>DB: SUM(size) WHERE user_id=user123 AND is_reference=False
    DB-->>Q: current_usage
    Q-->>V: ok
    V->>E: encrypt_file(file_obj)
    E-->>V: (ciphertext, iv)
    V->>FS: write ciphertext
    V->>DB: INSERT File record (transaction.atomic)
    V-->>C: 201 {id, file_hash, is_reference: false, ...}
```

## Upload — Duplicate File (Deduplication path)

```mermaid
sequenceDiagram
    participant C as Client
    participant V as FileViewSet
    participant D as DeduplicationService
    participant DB as SQLite


    C->>V: POST /api/files/ (same file bytes)
    V->>D: compute_hash(file_obj)
    D-->>V: sha256_hex (same hash)
    V->>D: find_duplicate(user123, sha256_hex)
    D->>DB: SELECT WHERE user_id=user123 AND file_hash=sha256_hex
    DB-->>D: <original File record>
    D-->>V: original (duplicate found)
    V->>D: create_reference(user123, original, filename)
    D->>DB: INSERT File (is_reference=True, original_file=original.id)
    D->>DB: UPDATE original SET reference_count = reference_count + 1
    D-->>V: reference File record
    V-->>C: 201 {id, is_reference: true, original_file: original.id, ...}
    Note over C,DB: Zero bytes written to disk
```

## Rate Limit Breach
```mermaid
sequenceDiagram
    participant C as Client
    participant TH as SlidingWindowThrottle
    participant Redis as Redis sorted set


    C->>TH: Request 1 (t=0.0s)
    TH->>Redis: EVAL Lua: ZREMRANGEBYSCORE, ZCARD=0, ZADD 0.0, EXPIRE
    Redis-->>TH: 1 (allowed)
    TH-->>C: pass → 200


    C->>TH: Request 2 (t=0.4s)
    TH->>Redis: EVAL Lua: prune, ZCARD=1, ZADD 0.4, EXPIRE
    Redis-->>TH: 1 (allowed)
    TH-->>C: pass → 200


    C->>TH: Request 3 (t=0.7s)
    TH->>Redis: EVAL Lua: prune, ZCARD=2 >= limit, do not ZADD
    Redis-->>TH: 0 (denied)
    TH-->>C: 429 {detail: 'Call Limit Reached'}


    C->>TH: Request 4 (t=1.1s)
    TH->>Redis: EVAL Lua: prune old scores, ZCARD=1, ZADD 1.1, EXPIRE
    Redis-->>TH: 1 (allowed)
    TH-->>C: pass → 200
```

## Delete — Original with References
```mermaid
sequenceDiagram
    participant C as Client
    participant V as FileViewSet
    participant D as DeduplicationService
    participant DB as SQLite
    participant FS as File Storage


    C->>V: DELETE /api/files/{original_id}/
    V->>DB: GET File WHERE id=original_id AND user_id=user123
    DB-->>V: original (is_reference=False, reference_count=2)
    V->>D: promote_reference(original)
    D->>DB: SELECT oldest reference WHERE original_file=original_id
    DB-->>D: ref1 (oldest)
    Note over D,DB: transaction.atomic()
    D->>DB: UPDATE ref1: is_reference=False, file=original.file, original.reference_count-=1
    D->>DB: UPDATE remaining refs: original_file=ref1.id
    D->>DB: DELETE original record
    V-->>C: 204 No Content
    Note over C,FS: Physical file retained — ref1 now owns it
```

## GET /api/files/ — List Files

```mermaid
sequenceDiagram
    participant C as Client
    participant MW as UserIdMiddleware
    participant TH as SlidingWindowThrottle
    participant V as FileViewSet
    participant Q as FileQueryService
    participant F as FileFilter (django-filter)
    participant DB as SQLite

    C->>MW: GET /api/files/?search=doc&file_type=text/plain&min_size=100&page=2
    MW->>TH: pass (UserId attached)
    TH->>V: pass (rate ok)
    V->>Q: build_queryset(user_id, filters)
    Q->>F: apply FileFilter(params, queryset)
    Note over F: Applies AND conditions:<br/>user_id=X<br/>original_filename__icontains="doc"<br/>file_type__exact="text/plain"<br/>size__gte=100
    F->>DB: SELECT * FROM files WHERE user_id=? AND original_filename LIKE ? AND file_type=? AND size >= ?
    DB-->>F: matching rows
    F-->>Q: filtered QuerySet
    Q-->>V: QuerySet
    V->>V: PageNumberPagination.paginate_queryset(qs, page=2)
    V-->>C: 200 { count, next, previous, results: [...] }
```

---

## GET /api/files/storage_stats/ — Storage Statistics

```mermaid
sequenceDiagram
    participant C as Client
    participant MW as UserIdMiddleware
    participant TH as SlidingWindowThrottle
    participant V as FileViewSet
    participant Q as FileQueryService
    participant DB as SQLite

    C->>MW: GET /api/files/storage_stats/ (UserId: user123)
    MW->>TH: pass (UserId attached)
    TH->>V: pass (rate ok)
    V->>Q: get_storage_stats(user_id="user123")

    Q->>DB: SELECT SUM(size) FROM files<br/>WHERE user_id='user123' AND is_reference=False
    DB-->>Q: total_storage_used (e.g. 5120 bytes)

    Q->>DB: SELECT SUM(size) FROM files<br/>WHERE user_id='user123'
    DB-->>Q: original_storage_used (e.g. 10240 bytes)

    Q->>Q: storage_savings = 10240 - 5120 = 5120
    Q->>Q: savings_percentage = (5120 / 10240) * 100 = 50.0
    Note over Q: If original_storage_used == 0 → savings_percentage = 0.0

    Q-->>V: { total_storage_used, original_storage_used, storage_savings, savings_percentage }
    V-->>C: 200 { user_id: "user123", total_storage_used: 5120, original_storage_used: 10240, storage_savings: 5120, savings_percentage: 50.0 }
```

---

## GET /api/files/file_types/ — Available File Types

```mermaid
sequenceDiagram
    participant C as Client
    participant MW as UserIdMiddleware
    participant TH as SlidingWindowThrottle
    participant V as FileViewSet
    participant Q as FileQueryService
    participant DB as SQLite

    C->>MW: GET /api/files/file_types/ (UserId: user123)
    MW->>TH: pass (UserId attached)
    TH->>V: pass (rate ok)
    V->>Q: get_file_types(user_id="user123")

    Q->>DB: SELECT DISTINCT file_type FROM files<br/>WHERE user_id='user123'<br/>ORDER BY file_type ASC
    DB-->>Q: ["application/pdf", "image/jpeg", "text/plain"]

    Q-->>V: sorted list of MIME type strings
    V-->>C: 200 ["application/pdf", "image/jpeg", "text/plain"]

    Note over C,DB: Returns only MIME types for this user.<br/>No cross-user leakage.
```

---

## GET /api/files/{id}/download/ — Download File
```mermaid
sequenceDiagram
    participant C as Client
    participant MW as UserIdMiddleware
    participant TH as SlidingWindowThrottle
    participant V as FileViewSet
    participant E as EncryptionService
    participant DB as SQLite
    participant FS as "File Storage /app/media/"

    C->>MW: GET /api/files/{id}/download/ (UserId: user123)
    MW->>TH: pass (UserId attached)
    TH->>V: pass (rate ok)

    V->>DB: SELECT * FROM files WHERE id={id} AND user_id='user123'

    alt File not found or belongs to another user
        DB-->>V: None
        V-->>C: 404 Not Found
        Note over C,V: 404 used (not 403) — avoids leaking<br/>existence of other users' files
    else File found
        DB-->>V: File record (with encryption_iv)
        V->>FS: read ciphertext from record.file.path
        FS-->>V: ciphertext bytes
        V->>E: decrypt_file(ciphertext, record.encryption_iv)
        E->>E: load ENCRYPTION_KEY from env var
        E->>E: reconstruct AES cipher with stored IV
        E-->>V: plaintext bytes
        V-->>C: 200 StreamingHttpResponse, Content-Disposition attachment
    end
```
