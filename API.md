# Document Portal — API Reference

Base URL (this deployment): `http://192.168.80.52:2311`

Two kinds of surface:

| Surface | Auth | Who uses it |
| --- | --- | --- |
| **JSON API** (`/v1/...`) | `Authorization: Bearer <api_key>` | Your apps / staff tooling |
| **Web pages** (`/staff`, `/u/...`, `/files/...`) | none (public) | Staff + clients in a browser |

### Getting your API key
The key belongs to the tenant. On the server:
```bash
docker exec doc_uploading_db psql -U postgres -d doc_uploading -tAc "SELECT name, api_key FROM tenant;"
```
Send it on every `/v1` call as `Authorization: Bearer <api_key>`.

> **PowerShell note:** bare `curl` is an alias for `Invoke-WebRequest` and mangles `-H`. Use **`curl.exe`** or the `Invoke-RestMethod` snippets below.

> **What's active on this deployment:** documents are stored on the **local filesystem**. The S3 + Celery/OCR features exist in the code but are **not enabled here** (no S3 bucket, no worker). Those endpoints are marked ⚠️ below.

---

## 1. Collection Portal API

### 1.1 Generate links for a case
`POST /v1/document-requests`

Mints one upload link per document type (27 total) for a client reference.

**Body**
| Field | Type | Required |
| --- | --- | --- |
| `name` | string | yes |
| `reference` | string | yes |

**curl**
```bash
curl -s -X POST "http://192.168.80.52:2311/v1/document-requests" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Theresa Topp","reference":"324991"}'
```

**PowerShell**
```powershell
$body = @{ name="Theresa Topp"; reference="324991" } | ConvertTo-Json
Invoke-RestMethod -Method Post "http://192.168.80.52:2311/v1/document-requests" `
  -Headers @{ Authorization = "Bearer $API_KEY" } -Body $body -ContentType "application/json"
```

**Response `201`** (27 links; abbreviated)
```json
{
  "batch_id": "b2c1e0a4-....-uuid",
  "reference": "324991",
  "client_name": "Theresa Topp",
  "links": [
    {
      "doc_type": "id_proof",
      "label": "ID Proof (Legacy)",
      "url": "http://192.168.80.52:2311/u/324991/id_proof/9fXa...token",
      "token": "9fXa...token",
      "status": "pending",
      "expires_at": "2026-07-27T10:46:37.389228Z"
    }
    // ... 27 items, one per document type
  ]
}
```

---

### 1.2 List a case's links
`GET /v1/document-requests?reference=<ref>`

Returns every link for a case, each with its status and (if uploaded) the document's preview URL.

**Query params**
| Param | Type | Notes |
| --- | --- | --- |
| `reference` | string | **required** — the case reference |
| `status` | string | optional — `pending` \| `submitted` \| `expired` |
| `uploaded_only` | bool | optional — `true` = only links that have an uploaded document |

**curl — only links that have uploads**
```bash
curl -s "http://192.168.80.52:2311/v1/document-requests?reference=324991&uploaded_only=true" \
  -H "Authorization: Bearer $API_KEY"
```

**PowerShell**
```powershell
Invoke-RestMethod "http://192.168.80.52:2311/v1/document-requests?reference=324991&uploaded_only=true" `
  -Headers @{ Authorization = "Bearer $API_KEY" }
```

**Response `200`**
```json
{
  "reference": "324991",
  "total": 1,
  "results": [
    {
      "doc_type": "immigration_status",
      "label": "Immigration Status",
      "upload_url": "http://192.168.80.52:2311/u/324991/immigration_status/lVJH...token",
      "token": "lVJH...token",
      "status": "submitted",
      "expires_at": "2026-07-27T10:46:37.389228Z",
      "document": {
        "doc_id": "6f1e3a3d-cf08-4cf9-99c5-170dfc5cb42f",
        "file_name": "image (5).png",
        "mime_type": "image/png",
        "file_url": "http://192.168.80.52:2311/files/<tenant>/324991/<req>/original__image (5).png",
        "uploaded_at": "2026-07-20T10:55:06.732986Z"
      }
    }
  ]
}
```

- `document` is `null` for links not yet uploaded.
- Drop `uploaded_only` (`?reference=324991`) to get **all** links for the case.
- `file_url` may contain spaces from the filename — encode as `%20` in a browser.

---

## 2. Documents API (read)

These work with the locally-stored portal documents.

### 2.1 List documents for a case
`GET /v1/documents?owner_ref=<ref>`

**Query params** (at least one required): `owner_ref`, `document_type`, `group_tag`, `ocr_status`.
Plus `limit` (1–200, default 50) and `offset` (default 0).

**curl**
```bash
curl -s "http://192.168.80.52:2311/v1/documents?owner_ref=324991" \
  -H "Authorization: Bearer $API_KEY"
```

**Response `200`**
```json
{
  "results": [
    {
      "id": "6f1e3a3d-cf08-4cf9-99c5-170dfc5cb42f",
      "tenant_id": "d97dd4b2-4878-47f2-9560-495f542b5af4",
      "owner_ref": "324991",
      "file_name": "image (5).png",
      "document_type": "immigration_status",
      "original_document_type": "immigration_status",
      "mime_type": "image/png",
      "file_size": 106236,
      "s3_url": "http://192.168.80.52:2311/files/.../original__image (5).png",
      "s3_url_ocr_json": null,
      "group_tag": null,
      "ocr_status": "not_requested",
      "ocr_text": null,
      "ocr_error": null,
      "base64_status": "pending",
      "base64_error": null,
      "metadata_": {},
      "uploaded_by": "Theresa Topp",
      "created_at": "2026-07-20T10:55:06.732986Z",
      "updated_at": "2026-07-20T10:55:06.732986Z"
    }
  ],
  "total": 1,
  "next_offset": null
}
```
The **`s3_url`** field is the inline preview link.

### 2.2 Get one document
`GET /v1/documents/{doc_id}`
```bash
curl -s "http://192.168.80.52:2311/v1/documents/6f1e3a3d-cf08-4cf9-99c5-170dfc5cb42f" \
  -H "Authorization: Bearer $API_KEY"
```
Returns a single document object (same shape as an item in 2.1). `404` if not found for your tenant.

### 2.3 Stream / preview a document file
`GET /v1/documents/{doc_id}/file`

Auth-proxied stream, served **inline** with the correct content type (browser previews it).
```bash
curl -s "http://192.168.80.52:2311/v1/documents/6f1e3a3d-cf08-4cf9-99c5-170dfc5cb42f/file" \
  -H "Authorization: Bearer $API_KEY" -o out.png
```
> Public alternative (no auth): open the document's `s3_url` (`/files/...`) directly in a browser.

### 2.4 Update a document
`PATCH /v1/documents/{doc_id}`

Editable fields only: `document_type`, `metadata`, `uploaded_by`. Optional `?metadata_mode=merge|replace` (default `merge`).
```bash
curl -s -X PATCH "http://192.168.80.52:2311/v1/documents/<doc_id>" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"document_type":"proof_of_address","metadata":{"note":"reclassified"}}'
```
Returns the updated document object.

### 2.5 OCR text status
`GET /v1/documents/{doc_id}/text`

Returns OCR status/text. For portal docs (OCR not run) this returns:
```json
{ "ocr_status": "not_requested", "ocr_text": null, "ocr_error": null }
```

---

## 3. Web pages (browser, no auth)

| Page | Method | Purpose |
| --- | --- | --- |
| `/staff` | GET | Enter name + reference → generate links |
| `/staff/generate` | POST (form) | Renders the generated links as upload tabs |
| `/staff/documents?reference=<ref>` | GET | View a case's uploaded docs + preview buttons |
| `/u/{reference}/{doc_type}/{token}` | GET | Client upload page (live / submitted / expired) |
| `/u/{reference}/{doc_type}/{token}` | POST (multipart `file`) | Submit an upload (returns JSON `{ok, message, doc_id}`) |
| `/files/{key}` | GET | Public inline file preview |

**Programmatic upload to a link** (what the upload page does under the hood):
```bash
curl -s -X POST "http://192.168.80.52:2311/u/324991/immigration_status/<token>" \
  -F "file=@/path/to/photo.png;type=image/png"
```
Response: `{"ok":true,"message":"Uploaded photo.png.","doc_id":"..."}` — link is then spent (one-time; re-POST returns `409`).

Allowed file types: PDF, DOC/DOCX, XLS/XLSX, JPG, PNG, WebP. Max size: 10 MB.

---

## 4. Health / ops (no auth)

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness — always 200 if process is up |
| `GET /health/db` | 200 if the database is reachable |
| `GET /ready` | 200 only if DB (and Redis, if used) are reachable; 503 otherwise |

```bash
curl -s "http://192.168.80.52:2311/health"
```

---

## 5. Not enabled on this deployment ⚠️

These endpoints exist in the code but require **S3** and/or the **Celery/OCR worker**, which are not running here (local-storage mode). They will error until configured:

| Endpoint | Needs |
| --- | --- |
| `POST /v1/documents` (direct upload) | S3 bucket. Use the portal `/u/...` upload instead. |
| `DELETE /v1/documents/{id}` | S3 (deletes S3 prefix) |
| `POST /v1/documents/{id}/ocr` | Celery worker + OCR provider |
| `GET /v1/documents/{id}/ocr-json` | OCR to have run (returns 404 otherwise) |

---

## Quick reference — the two you asked for

```bash
# 1) Generate all links for a case
curl -s -X POST "http://192.168.80.52:2311/v1/document-requests" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"Theresa Topp","reference":"324991"}'

# 2) List a case's links that have uploads
curl -s "http://192.168.80.52:2311/v1/document-requests?reference=324991&uploaded_only=true" \
  -H "Authorization: Bearer $API_KEY"
```
