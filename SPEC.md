# Document Microservice — Build Spec

A generic, multi-tenant document storage and processing microservice. Extracted
from the document-handling features of `case-assessment-backend` so that
multiple internal applications can share one service for upload, OCR, S3
storage, and lightweight document metadata.

This document is the complete build specification. An implementing agent
should treat the schema, API contract, and Celery notification contract as
authoritative — anything not specified here is a free design choice.

---

## 1. Purpose and scope

The microservice owns the boundary between "raw uploaded file" and "structured
document record." Specifically:

- Receives file uploads from caller applications.
- Stores files in S3.
- Optionally OCRs them.
- Stores per-file metadata in Postgres.
- Notifies the caller application asynchronously (via Celery) when processing
  finishes, so the caller can run its own domain-specific logic.

The first caller is `case-assessment-backend` (UK insolvency case ETL). Future
callers may be other internal tools. The service must be **generic** — no
case-assessment vocabulary, no application-specific validation, no hardcoded
document types.

### In scope

| Feature | What it means |
| --- | --- |
| Multi-tenant document storage | One row per tenant in `tenant` table; every document scoped to a tenant |
| File upload to S3 | Multipart upload, file lands in S3, row created in Postgres |
| Optional OCR | Caller specifies `ocr=true|false` per upload. When true, OCR runs async and result is stored in S3 + indexed in Postgres |
| Document type re-classification | `PATCH /v1/documents/<id>` updates `document_type`; `original_document_type` is frozen at upload |
| Filename-based dedup | UNIQUE constraint on `(tenant_id, owner_ref, file_name, document_type)`; re-upload = UPDATE |
| Multi-page batch grouping | Caller assigns a `group_tag` UUID and reuses it across pages |
| Stashing raw OCR JSON in S3 | When OCR runs, the full raw response is written to S3 alongside the original file |
| Cross-service notifications via Celery | When processing completes, microservice fires a Celery task to the tenant's broker so consumers can react |

### Out of scope (intentionally NOT built here)

- Identity validation (name / DOB / address matching against a customer record).
  Callers do this on their own data after fetching the OCR text.
- Application-level rollup verdicts (PASSED / FAILED / WARNING).
- Hardcoded `document_type` vocabulary. The column is a free-text string.
  Callers define their own values.
- Workflow orchestration beyond upload + OCR. Anything that needs to "wait for
  3 docs and then do X" is the caller's problem — they listen to the Celery
  notifications and orchestrate.
- Document-specific extractors (payslip parsing, bank-statement breakdowns,
  CIS slip AI extraction). Those live in the caller; the microservice only
  hands over raw OCR text + the JSON.

---

## 2. Architecture overview

```
                                      caller frontends (browser)
                                              │
                                              │  HTTPS (multipart)
                                              ↓
                              ┌────────────────────────────────┐
                              │   Document Microservice (API)  │
                              │                                │
                              │   FastAPI / Django / etc       │
                              │   - 7 REST endpoints           │
                              │   - tenant auth via api_key    │
                              └──────┬─────────────────┬───────┘
                                     │                 │
                              ┌──────┴───┐       ┌─────┴─────┐
                              │ Postgres │       │     S3    │
                              │ (metadata│       │ (original │
                              │  + URLs) │       │ + ocr.json│
                              └──────────┘       └───────────┘
                                     │
                                     │  Celery worker (internal)
                                     │  - runs OCR on `ocr_status=pending` jobs
                                     │  - writes ocr.json to S3
                                     │  - updates row
                                     │  - fires notification task to tenant broker
                                     ↓
                              ┌──────────────────────┐
                              │  Redis (per-tenant   │
                              │  broker — DB 0, 1,…) │
                              └──────────┬───────────┘
                                         │
                                         ↓
                              caller backend's celery worker
                              (listens for `microservice.doc_processed`)
                              - reads payload (s3 urls + metadata)
                              - fetches OCR JSON from S3
                              - runs caller's domain logic
```

**Two interfaces:**

- **HTTP API (control plane).** Synchronous, REST. For upload, fetch, list,
  PATCH, delete, OCR re-run. Used by frontends and any consumer that wants to
  poll.
- **Celery task notifications (event plane).** Asynchronous. Microservice fires
  a task to the tenant's broker when a doc finishes processing. Consumers
  subscribe and run domain-specific follow-up logic.

The two interfaces are independent. A caller can use one or both.

---

## 3. Tech stack (recommended defaults)

The implementing agent is free to swap any of these, but these are the
defaults assumed by the rest of this spec:

| Component | Default | Notes |
| --- | --- | --- |
| Web framework | FastAPI | Async-friendly, OpenAPI auto-generated, lighter than Django for a single-purpose service. Django REST Framework is acceptable if the agent strongly prefers it. |
| Database | Postgres 15+ | `gen_random_uuid()` is built-in. `jsonb` for metadata. |
| Task queue | Celery 5+ | The internal OCR worker AND the cross-service notification publisher both use Celery. |
| Broker | Redis 7+ | One Redis instance. Different DB numbers per tenant for notification broker; default DB for the microservice's own internal queue. |
| Object storage | AWS S3 | Or any S3-compatible service (MinIO, R2, etc.). |
| OCR provider | OCR.space | First and only provider. The OCR call should be wrapped in a single function so a second provider (Textract, Tesseract) can be added later without refactoring callers. |
| Python | 3.11+ | |

---

## 4. Database schema

Two tables. Postgres. No S3 columns — the full S3 URL is stored on each
document row at upload time and never re-computed.

```sql
-- =======================================================
-- tenant — one row per consumer application
-- =======================================================
CREATE TABLE tenant (
    id          uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text          NOT NULL,                          -- "case-assessment-backend"
    api_key     text          NOT NULL UNIQUE,                   -- bearer token sent on every request
    broker_url  text          NOT NULL,                          -- "redis://host:6379/0" — where to publish notifications
    created_at  timestamptz   NOT NULL DEFAULT now()
);

-- =======================================================
-- document — the main entity
-- =======================================================
CREATE TABLE document (
    id                       uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                uuid          NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    owner_ref                text          NOT NULL,                          -- "application:332591", opaque to service
    file_name                text          NOT NULL,
    document_type            text          NOT NULL,                          -- caller-defined; mutable via PATCH
    original_document_type   text          NOT NULL,                          -- snapshot of document_type at upload; never updated
    mime_type                text          NOT NULL,
    file_size                bigint        NOT NULL,
    s3_url                   text          NOT NULL,                          -- full URL of original file; stored, never re-computed
    s3_url_ocr_json          text          NULL,                              -- full URL of raw OCR JSON; NULL until OCR completes
    group_tag                uuid          NULL,                              -- multi-page batch identifier
    ocr_status               text          NOT NULL DEFAULT 'not_requested',  -- 'not_requested' | 'pending' | 'done' | 'failed'
    ocr_text                 text          NULL,                              -- flat OCR text; populated when ocr_status='done'
    ocr_error                text          NULL,                              -- failure detail for OCR or upload errors
    metadata                 jsonb         NOT NULL DEFAULT '{}',             -- caller's escape hatch for arbitrary fields
    uploaded_by              text          NOT NULL,                          -- opaque caller user id
    created_at               timestamptz   NOT NULL DEFAULT now(),
    updated_at               timestamptz   NOT NULL DEFAULT now(),

    -- Filename-based dedup: re-uploading the same filename for the same
    -- owner+type UPDATES the existing row (does not create a duplicate).
    UNIQUE (tenant_id, owner_ref, file_name, document_type)
);

CREATE INDEX document_owner_idx ON document (tenant_id, owner_ref);
CREATE INDEX document_group_idx ON document (group_tag) WHERE group_tag IS NOT NULL;
```

### Column intent

| Column | Purpose |
| --- | --- |
| `id` | Primary key. Returned to caller, who stores it to reference the doc later. |
| `tenant_id` | Multi-tenant scope. Every query filters by this. |
| `owner_ref` | Opaque to service. Caller uses it to associate docs with their own entity (e.g. an application or user). |
| `file_name` | Original filename. Part of dedup key. Also embedded in derived S3 key (see section 5). |
| `document_type` | Caller-defined free-text string ("bank_statement", "passport", "anything"). PATCH-able for re-classification. |
| `original_document_type` | Frozen at upload. Lets callers know what was originally submitted vs the current (possibly re-classified) type. |
| `mime_type` | Used during upload to set S3 object Content-Type and validate against an allowlist. |
| `file_size` | Returned to caller; used for upload size guards. |
| `s3_url` | The link. Stored once at upload, never recomputed. Returned to caller verbatim. |
| `s3_url_ocr_json` | URL of the raw OCR JSON in S3. NULL until OCR has run. |
| `group_tag` | Multi-page batch grouping. Caller assigns the same UUID on every page. Indexed for fast "fetch all pages of group X" queries. |
| `ocr_status` | Lifecycle: `not_requested` (caller didn't ask), `pending` (queued), `done` (OCR complete), `failed`. |
| `ocr_text` | Flat extracted text. Populated when `ocr_status='done'`. Useful for cheap text retrieval without hitting S3. |
| `ocr_error` | Failure detail when `ocr_status='failed'` or when initial upload had an error. |
| `metadata` | jsonb. Caller's escape hatch for any extra fields they want to associate (notes, custom flags, classification reasons, etc.). Service does not interpret the contents. |
| `uploaded_by` | Opaque caller user id. Required on every upload. |

### Constraints

- `UNIQUE (tenant_id, owner_ref, file_name, document_type)` — filename-based
  dedup. Re-uploading the same filename for the same owner+type updates the
  existing row (does not create a duplicate). This matches the behaviour of
  `case-assessment-backend`'s current `ingest_file` function.

### Indexes

- `document_owner_idx (tenant_id, owner_ref)` — most common query is "list all
  docs for owner X."
- `document_group_idx (group_tag) WHERE group_tag IS NOT NULL` — partial index
  for the multi-page-fetch query. Keeps the index small.

---

## 5. S3 layout

The bucket name and AWS credentials live in environment variables, not in
the database. The per-file S3 KEY is derived from a deterministic convention.
The full URL is **stored** in the `document` row at upload time so it never
has to be re-computed.

### Path convention

```
<env-bucket>/<tenant_id>/<owner_ref>/<doc_id>/original__<file_name>
<env-bucket>/<tenant_id>/<owner_ref>/<doc_id>/ocr.json
```

Example for a passport upload by case-assessment for application 332591:

```
my-bucket/eb2c…/application:332591/9a1c…/original__passport.pdf
my-bucket/eb2c…/application:332591/9a1c…/ocr.json
```

### Why this convention

- `<doc_id>` in the path means every doc has a unique S3 prefix even if the
  same filename is re-uploaded (after a delete + re-create).
- Including `<tenant_id>` makes per-tenant ACL policies trivial later (e.g.
  IAM roles that can only access their own prefix).
- `<owner_ref>` groups all docs of one application/user under one S3 prefix,
  useful for bulk operations.

### Privacy caveat (must be resolved before production)

Storing `s3_url` without time-limited signing only works if the bucket is
publicly readable OR is behind a CDN/proxy that handles auth. For sensitive
PII (passports, bank statements, payslips) under UK GDPR, **public-read is
not acceptable**.

Three viable patterns:

1. **Public bucket** — only for non-sensitive deployments. Not recommended.
2. **Private bucket + CDN with signed cookies** — URL stays stable, access is
   controlled at the edge. The stored URL points at the CDN, not S3 directly.
3. **Private bucket + auth proxy** — stored URL points at a microservice
   endpoint like `GET /v1/documents/<id>/file` which streams from S3 with
   tenant auth. The S3 object itself is private.

**Decision required from the user before launch.** The implementing agent
should pick the simplest of these that meets the security bar (option 3 is
the safest default for sensitive data).

---

## 6. HTTP API

All endpoints under `/v1/`. All requests require an `Authorization: Bearer
<api_key>` header. The api_key resolves to a tenant; the tenant id is never in
the URL or body.

### 6.1 Upload

```
POST /v1/documents
Content-Type: multipart/form-data

Form fields:
    file:           <binary>                      required
    owner_ref:      "application:332591"          required
    document_type:  "bank_statement"              required, free string
    uploaded_by:    "user_abc"                    required, opaque
    ocr:            "true" | "false"              optional, default "false"
    group_tag:      "<uuid>"                      optional, multi-page batch
    metadata:       <JSON-encoded string>         optional

Returns 201 Created on new upload, 200 OK if filename+owner+type already
existed (row was updated, not duplicated).

Response body (both 201 and 200):
{
  "id": "9a1c…",
  "tenant_id": "eb2c…",
  "owner_ref": "application:332591",
  "file_name": "passport.pdf",
  "document_type": "passport",
  "original_document_type": "passport",
  "mime_type": "application/pdf",
  "file_size": 1234567,
  "s3_url": "https://...",
  "s3_url_ocr_json": null,
  "group_tag": null,
  "ocr_status": "pending",       // or "not_requested" if ocr=false
  "ocr_text": null,
  "ocr_error": null,
  "metadata": {},
  "uploaded_by": "user_abc",
  "created_at": "2026-05-16T12:00:00Z",
  "updated_at": "2026-05-16T12:00:00Z"
}
```

Server-side validation:

- `mime_type` in an allowlist (PDF, DOC, DOCX, JPG, PNG, WEBP, XLS, XLSX).
- `file_size` under a configured maximum (default 10 MB).
- All required form fields present.

### 6.2 Get one

```
GET /v1/documents/<id>

Returns 200 OK with the full row (same shape as upload response).
Returns 404 if the doc doesn't exist or belongs to a different tenant.
```

### 6.3 List

```
GET /v1/documents
    ?owner_ref=application:332591
    &group_tag=<uuid>
    &document_type=bank_statement
    &ocr_status=done
    &limit=50
    &offset=0

At least ONE filter parameter is required. Unfiltered scans across a tenant
are refused (returns 400) to prevent accidental full-table reads.

Response:
{
  "results": [ {…doc…}, {…doc…} ],
  "total":   42,
  "next_offset": 50
}
```

### 6.4 Update (re-classify, etc.)

```
PATCH /v1/documents/<id>
Content-Type: application/json

{
  "document_type": "cis_slip",        // re-classification
  "metadata":      { "any": "data" }, // SHALLOW MERGE into existing metadata
  "uploaded_by":   "user_xyz"         // rare but allowed
}

Returns 200 OK with the updated row.

Immutable fields (PATCH attempts return 400):
  file_name, owner_ref, s3_url, s3_url_ocr_json, file_size,
  mime_type, original_document_type, ocr_status, ocr_text,
  ocr_error, tenant_id, id, created_at

metadata shallow-merge:
  - Existing { "a": 1, "b": 2 } + PATCH { "metadata": { "b": 99, "c": 3 } }
  - Result:  { "a": 1, "b": 99, "c": 3 }
  - To replace instead of merge, pass ?metadata_mode=replace (optional).
```

### 6.5 Delete

```
DELETE /v1/documents/<id>

Returns 204 No Content.

Cascades:
  - Deletes the original file from S3.
  - Deletes the ocr.json from S3 (if present).
  - Deletes the DB row.

No soft-delete by default. If you later want it, add a `deleted_at` column.
```

### 6.6 Trigger or re-run OCR

```
POST /v1/documents/<id>/ocr
Content-Type: application/json

{ "force": false }

  - If ocr_status is currently "not_requested" or "failed": queues OCR.
    Returns 202 Accepted with ocr_status="pending".
  - If ocr_status is "done":
      - force=false (default): no-op, returns 200 OK with current state.
      - force=true: re-queues OCR, returns 202 Accepted.
  - If ocr_status is "pending": no-op, returns 200 OK.
```

### 6.7 Get just the OCR text

```
GET /v1/documents/<id>/text

  - ocr_status="done":    200 OK  { "ocr_status": "done", "ocr_text": "..." }
  - ocr_status="pending": 202 Accepted { "ocr_status": "pending" }
  - ocr_status="failed":  422 Unprocessable  { "ocr_status": "failed", "ocr_error": "..." }
  - doc not found:        404
```

Convenience endpoint. The same data is available via `GET /v1/documents/<id>`
but this avoids transferring the whole row when the caller only wants text.

---

## 7. Cross-service notifications (Celery)

When document processing finishes (OCR completes, or upload completes with
`ocr=false`), the microservice publishes a Celery task to the tenant's broker.
Consumers subscribe and react.

### Task name

```
microservice.doc_processed
```

This name is **identical across all tenants**. Routing to the correct
consumer happens via the Redis DB number in the tenant's broker URL.

### Payload contract

```json
{
  "schema_version": 1,
  "event":          "doc_processed",
  "doc_id":         "9a1c…",
  "tenant_id":      "eb2c…",
  "owner_ref":      "application:332591",
  "file_name":      "passport.pdf",
  "document_type":  "passport",
  "original_document_type": "passport",
  "s3_url":         "https://...",
  "s3_url_ocr_json": "https://..." | null,
  "ocr_status":     "done" | "not_requested" | "failed",
  "group_tag":      "<uuid>" | null,
  "uploaded_by":    "user_abc",
  "metadata":       { ... }
}
```

**`schema_version` is mandatory.** Consumers MUST check this before parsing.
Adding new fields is non-breaking; renaming or removing requires a version
bump. Microservice releases that change the payload increment this number.

The payload size stays kilobytes. Heavy data (the OCR JSON) is fetched by
consumers from `s3_url_ocr_json` directly.

### When the task is fired

- After a successful upload with `ocr=false`: fire once with
  `ocr_status="not_requested"`.
- After OCR completes successfully: fire once with `ocr_status="done"`.
- After OCR fails: fire once with `ocr_status="failed"` and `ocr_error`
  populated.
- On manual `POST /v1/documents/<id>/ocr` re-run: fire again on completion.

### Per-tenant Redis routing

Each tenant has a `broker_url` column. The microservice maintains a small
client cache:

```python
from celery import Celery

_clients: dict[str, Celery] = {}

def _client_for(broker_url: str) -> Celery:
    if broker_url not in _clients:
        _clients[broker_url] = Celery(broker=broker_url)
    return _clients[broker_url]

def notify_doc_processed(tenant, document):
    payload = {
        "schema_version": 1,
        "event": "doc_processed",
        "doc_id": str(document.id),
        # …
    }
    _client_for(tenant.broker_url).send_task(
        "microservice.doc_processed",
        args=[payload],
    )
```

### Consumer side (example)

In `case-assessment-backend`'s Celery worker:

```python
@celery_app.task(name="microservice.doc_processed")
def handle_doc_processed(payload: dict) -> None:
    if payload.get("schema_version") != 1:
        # log + skip or branch on version
        return
    s3_url = payload["s3_url"]
    ocr_json_url = payload["s3_url_ocr_json"]
    # fetch JSON from S3, run breakdown, write to local DB
```

The task NAME is the contract. Consumer registers with the same name and
listens on the broker URL the microservice publishes to.

### Why this works as a generic pattern

- Microservice doesn't know any consumer's domain logic — it just fires a
  named task with a documented payload.
- Adding a new tenant = adding a row to `tenant` with a different `broker_url`.
  Microservice code does not change.
- Each tenant's consumer registers `microservice.doc_processed` in their own
  Celery app pointed at their own broker URL. No cross-talk.

---

## 8. Auth

- Every request must include `Authorization: Bearer <tenant.api_key>`.
- `api_key` is a single high-entropy string (e.g. 32 bytes hex). Stored
  hashed-at-rest is preferable; if stored plaintext, the `tenant.api_key`
  column should be encrypted at the database level.
- Tenant is resolved from the api_key on every request. `tenant_id` is never
  in the URL or request body.
- Requests with missing or invalid api_key return `401 Unauthorized`.
- Requests authenticating as tenant A but querying a doc owned by tenant B
  return `404 Not Found` (never `403` — don't leak existence of other
  tenants' resources).

---

## 9. Multi-tenancy

- One Postgres database, multi-tenant via `tenant_id` column.
- Every query MUST filter by `tenant_id` derived from the authenticated
  api_key. Enforce this at the data-access layer (e.g. an ORM mixin / Django
  Manager / FastAPI dependency) so individual endpoint handlers can't forget.
- One S3 bucket, paths scoped by tenant_id (see section 5).
- One Redis instance shared by all internal microservice work. Tenant
  notification brokers are also Redis but typically on different DB numbers
  or different instances (controlled by `tenant.broker_url`).
- No tenant can ever see another tenant's data — enforced at the query layer
  and the S3 IAM policy.

---

## 10. OCR pipeline

### Trigger

OCR runs when:

1. Upload happens with `ocr=true`. Microservice sets `ocr_status='pending'`
   and enqueues an internal Celery task.
2. `POST /v1/documents/<id>/ocr` is called (with or without `force=true`).

### Worker logic

```
Task: ocr_document(doc_id)

1. Load row from DB. Bail if ocr_status != "pending".
2. Fetch original file from s3_url.
3. Send to OCR provider (OCR.space initially). Pass content-type and any
   table-mode flag based on document_type (see "table mode" below).
4. On success:
     - Upload full raw OCR JSON to S3 at <doc_id>/ocr.json.
     - Set s3_url_ocr_json to the URL.
     - Extract flat text and set ocr_text.
     - Set ocr_status = "done", ocr_error = NULL.
     - Save row.
     - Fire microservice.doc_processed task to tenant broker.
5. On failure:
     - Set ocr_status = "failed", ocr_error = "<reason>".
     - Save row.
     - Fire microservice.doc_processed task with status="failed".
```

### Table mode

OCR.space supports a "table mode" that preserves columns/rows for tabular
documents. The microservice uses table mode when the caller-supplied
`document_type` matches a configurable allowlist. Default allowlist:

```
bank_statement
creditor_statement
creditor_report
```

This allowlist is a microservice config value (env var or DB-backed). Callers
that use a different vocabulary can have their own allowlist configured at
tenant level later (out of scope for v1).

### Provider abstraction

Wrap the OCR call in a single function:

```python
def perform_ocr(file_bytes: bytes, content_type: str, is_table: bool) -> dict:
    """Return the raw provider response as a dict. Provider-agnostic."""
```

The body uses OCR.space initially. Swapping to Textract or Tesseract later
is a change inside this function only.

---

## 11. Contracts and invariants

### Immutable fields

The following columns are set at upload and never change:

- `id`, `tenant_id`, `original_document_type`, `file_name`, `s3_url`,
  `file_size`, `mime_type`, `created_at`

### Mutable fields

- `document_type` — via PATCH (re-classification).
- `metadata` — via PATCH (shallow merge by default, replace via query param).
- `uploaded_by` — via PATCH (rare but allowed).
- `ocr_status`, `ocr_text`, `ocr_error`, `s3_url_ocr_json` — only the
  microservice mutates these (via the OCR worker). Not exposed in PATCH.
- `updated_at` — auto-updated on every write.

### Dedup semantics

`UNIQUE (tenant_id, owner_ref, file_name, document_type)`:

- Re-uploading same filename for same owner+type: UPDATE the existing row.
  All mutable fields are overwritten with the new values. `original_document_type`
  is NOT updated. A fresh S3 object replaces the old one at the same key.
- Re-uploading same filename with a DIFFERENT `document_type`: creates a new
  row.

### schema_version evolution

The notification payload's `schema_version` starts at 1. Rules:

- Adding optional fields: do NOT bump version.
- Renaming or removing fields: bump version, support both during a grace
  window, document the migration.
- Consumers SHOULD branch on `schema_version` and log a warning when they
  see an unfamiliar version.

---

## 12. Edge cases

- **OCR retry policy.** The internal OCR worker should retry transient
  failures (network errors, OCR provider 5xx) up to 3 times with exponential
  backoff. Permanent failures (provider returns "unsupported file") fail
  immediately with `ocr_status='failed'`.
- **Concurrent OCR runs.** If `POST /v1/documents/<id>/ocr` is called while
  `ocr_status='pending'`, the call is a no-op (no new task queued). If
  called with `force=true` while pending, the existing task is allowed to
  finish; the force is ignored. (Optionally: implement task revocation. Not
  required for v1.)
- **Group_tag without a parent.** The microservice does NOT enforce that
  every group_tag has a "canonical" or "primary" document. Callers manage
  that themselves.
- **Tenant key rotation.** Updating `tenant.api_key` immediately invalidates
  the old key. There is no grace period in v1. Tenants must coordinate
  rotation themselves.
- **Bucket migration.** Because `s3_url` is stored, migrating buckets means
  updating every row. Provide an admin command for this; do not surface it
  via the API.

---

## 13. Build order (suggested phasing)

A reasonable order for the implementing agent:

1. **Skeleton + schema.** FastAPI app, Postgres connection, run the schema
   migration, basic health check endpoint.
2. **Auth.** Bearer-token middleware that resolves api_key to a tenant.
   Reject unauth requests.
3. **S3 layer.** Functions to upload a file, upload a JSON, build a public
   URL, delete by key. Wrapped so tests can mock them.
4. **Upload endpoint** (no OCR yet). POST /v1/documents stores file in S3,
   row in DB, returns the row.
5. **Get / list / patch / delete endpoints.** Round out the CRUD surface.
6. **OCR worker.** Internal Celery worker that runs OCR.space, writes
   ocr.json to S3, updates the row.
7. **OCR trigger endpoint.** POST /v1/documents/<id>/ocr + the `ocr=true`
   flag on upload.
8. **Notification task.** When OCR finishes (or upload finishes with ocr=false),
   fire `microservice.doc_processed` to the tenant broker.
9. **GET /v1/documents/<id>/text** convenience endpoint.
10. **Hardening.** Rate limiting per tenant, structured logging, metrics,
    health/readiness probes, dockerfile, deployment config.

Each step is independently testable.

---

## 14. What this microservice deliberately does NOT do

For clarity, the following are explicit non-goals:

- It does NOT validate document content against a customer record (name /
  DOB / address matching). That logic stays in the caller application.
- It does NOT compute application-level verdicts (PASSED / FAILED / WARNING).
- It does NOT enrich documents with case-specific structured data (payslip
  parsing, bank statement transaction extraction, etc.). It hands back the
  raw OCR JSON; callers run their own extractors.
- It does NOT know about handover, ETL, breakdown, or any other
  case-assessment workflow concept.
- It does NOT do user management beyond opaque `uploaded_by` strings.

If a feature smells like it belongs to a specific application, it does not
belong in this microservice.

---

## 15. Open questions for the user before launch

These need answers before production deployment:

1. **S3 access pattern for sensitive PII.** Public bucket / CDN with signed
   cookies / auth proxy through the microservice. See section 5.
2. **Provider choice.** OCR.space is the default — confirm it's still the
   target, or specify if Textract / Tesseract / something else should be the
   initial provider.
3. **Tenant key storage.** Plaintext, hashed, or KMS-encrypted at rest.
4. **Rate limits.** Per-tenant request and upload-size limits.
5. **Retention policy.** Are documents kept forever? Soft-deleted? Auto-purged
   after N days?

The implementing agent should flag these explicitly and either pick a sane
default or ask the user before committing to one.

---

## 16. Reference — the case-assessment current document module

For context, the existing document module being replaced lives at:

- `case_assesment/applications/services/documents.py` — the `ingest_file` and
  `ingest_files_parallel` functions. Single source of truth for upload + OCR +
  validation + S3 + DB persist in the current monolith.
- `case_assesment/applications/views/documents.py` — DocumentValidateView,
  DocumentCreateView, DocumentDetailView, DocumentPreviewView.
- `case_assesment/documents/views.py` — separate small Django app that owns
  `PresignedUploadView` and `ConfirmUploadView`.
- `case_assesment/applications/models.py` line 272+ — the `Document` model.

The new microservice replaces the **storage, OCR, dedup, grouping, and S3
parts** of the above. It does NOT replace the validation parts
(`validate_ocr_text`, `validate_file_against_client`, `aggregate_validation_status`)
— those stay in case-assessment-backend.

---

End of spec.
