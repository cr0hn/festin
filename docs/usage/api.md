# REST API reference

Base URL: `/api/v1` · Auth: `Authorization: Bearer <jwt>` · Errors: `{"error": "..."}`

## Authentication

### `POST /auth/login` — public

```bash
curl -s -X POST localhost:8420/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "secret"}'
```

```json
{"access_token": "eyJhbG...", "token_type": "bearer", "username": "admin", "role": "admin"}
```

Token: JWT HS256, 60-minute expiry, signed with `FESTIN_JWT_SECRET`.

### `POST /auth/register` — semi-public

| Situation | Result |
|---|---|
| Database has **0 users** (no header needed) | `201` — user created with role **admin** |
| Authenticated **admin** registers someone | `201` — role `viewer` |
| Authenticated **viewer** tries | `403` |
| Anonymous with populated database | `401` |

```json
{"id": 2, "username": "alice", "role": "viewer"}
```

## Health

### `GET /health` — public

```json
{"status": "ok", "pending": 2}
```

## Stats

### `GET /stats`

```json
{
  "scan_count": 13,
  "findings": {"total": 2, "critical": 1, "high": 1, "medium": 0, "low": 0},
  "recent_scans": [
    {"day": "2026-09-07", "scans": 11, "buckets": 0, "findings": 0, "critical": 0, "high": 0},
    {"day": "2026-09-06", "scans": 1, "buckets": 3, "findings": 2, "critical": 1, "high": 1}
  ]
}
```

`recent_scans` covers the last 14 active days (UTC).

## Projects

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/projects` | any | list with `domain_count`, `scan_count`, `findings_count`, `last_scan_at` |
| `POST` | `/projects` | admin | body `{"name": "...", "description": "..."}`; duplicate name → `409` |
| `GET` | `/projects/{id}` | any | `{"project": {...counts}, "domains": [...]}` |
| `PATCH` | `/projects/{id}` | admin | partial `name`/`description` |
| `DELETE` | `/projects/{id}` | admin | cascades domains, scans, findings, buckets; project `1` (default) → `400` |
| `POST` | `/projects/{id}/domains` | admin | `{"domain_name": "example.com"}`; global duplicate → `409` |

```bash
curl -s -X POST localhost:8420/api/v1/projects \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "prod-assets", "description": "Public origins"}'
```

## Domains

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/domains?project_id={id}` | any | filtered list |
| `DELETE` | `/domains/{id}` | admin | cascades its scans/findings/buckets |

## Scans

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/scans?project_id=&status=&limit=` | any | joined rows: `domain_name`, `project_name` |
| `GET` | `/scans/{id}` | any | full bundle: `{"scan": {...}, "findings": [...], "buckets": [...]}` |
| `DELETE` | `/scans/{id}` | any | deletes scan + its findings/buckets |
| `POST` | `/scans/run-scan` | any | see below |

### `POST /scans/run-scan`

```bash
curl -s -X POST localhost:8420/api/v1/scans/run-scan \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"domains": ["example.com"], "project_id": 2}'
```

Response `202`:

```json
{"scan_id": 14, "job_id": "job-1725700000000", "status": "accepted"}
```

The scan record is created immediately (status `pending` → `running` → `completed`/`failed`) and executed as a background task; buckets and findings are persisted when it finishes. `project_id` defaults to `1`.

## Findings & buckets

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/findings?severity=&limit=` | any | keys: `bucket`, `object`, `rule`, `severity`, `line`, `match` (redacted) |
| `GET` | `/buckets?limit=` | any | keys: `name`, `objects_count`, `scan_id` |

## Scheduled scans

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/queues/schedule` | any | key is **`scheduled`** (frozen contract) |
| `POST` | `/queues/schedule` | admin | `{"domain": "example.com", "interval_minutes": 60}` |
| `DELETE` | `/queues/schedule/{id}` | admin | |

## Users (admin only)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/users` | `{"users": [{"id", "username", "role"}]}` |
| `POST` | `/users` | `{"username", "password", "role": "viewer"\|"admin"}` |
| `PATCH` | `/users/{id}` | `{"role": "admin"}` |
| `DELETE` | `/users/{id}` | `400` if deleting yourself or the last admin |

## Status codes

| Code | Meaning |
|---|---|
| `200` | OK |
| `201` | Created |
| `202` | Scan accepted (async) |
| `400` | Validation (bad body, deleting default project / self / last admin) |
| `401` | Missing or invalid token |
| `403` | Authenticated but not admin |
| `404` | Unknown id |
| `409` | Duplicate (project or domain name) |