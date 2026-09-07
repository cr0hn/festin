# Runbook & troubleshooting

Operational reference. Context in [architecture](architecture.md); the API surface in [usage](../usage/api.md).

## Daily operation

```bash
# status
curl -s localhost:8420/api/v1/health
# {"status": "ok", "pending": 2}

# logs (docker)
docker compose logs -f festin

# restart
docker compose restart festin
```

## Credentials

| Situation | Action |
|---|---|
| Empty database | `POST /api/v1/auth/register` — first user becomes **admin** |
| Populated database | only an existing admin can create users (same endpoint with Bearer) |
| Admin password lost | wipe users and re-bootstrap: `DELETE FROM users;` in the DB, restart, register |
| Viewer needs admin | admin flips the role in **Users** view (or `PATCH /users/{id}`) |

```bash
# manual bootstrap
curl -X POST localhost:8420/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "CAMBIAME"}'
```

## Backup & restore

```bash
# SAFE backup while running (SQLite .backup API)
docker compose exec festin python -c \
  "import sqlite3; sqlite3.connect('/data/festin.db').backup('/data/backup.db')"
docker compose cp festin:/data/backup.db ./festin-$(date +%F).db

# restore: stop service, replace file, start
docker compose stop festin
docker cp festin-2026-09-07.db festin:/data/festin.db
docker compose start festin
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401` on register while logged in as admin | middleware exempting register (old bug) | verify `/auth/register` is NOT in `JWTMiddleware.DEFAULT_EXEMPT_PATHS` |
| Login fails in browser, curl works | stale token or residual register-mode in `localStorage` | `localStorage.clear()` + reload |
| Stale JS/CSS in the browser | asset cache | hard reload; assets are versioned `?v=N` — bump on change |
| Scan stuck in `running` | executor task died | check server logs; the row stays `running` (no watchdog yet) |
| Scheduled scans all fire after restart | `last_run` is in-memory | expected; first scheduler tick re-fires every schedule |
| `403 forbidden` on a mutation | viewer role | log in as admin, or `PATCH /users/{id}` |
| `409` creating project/domain | name already exists (domains are globally unique) | pick another name |
| Cannot delete project id 1 | default project is protected | by design |
| Cannot delete a user | it's you, or the last admin | promote another admin first |
| passlib/bcrypt crash | passlib 1.7 vs bcrypt 4.x | `auth.py` uses bcrypt directly — don't reinstall passlib |
| aiohttp middleware error "not callable" | old-style middleware | use the `_wrap_middleware()` pattern in `serve.py` |
| `uv run` fails parsing pyproject | `[dependency-groups]` TOML layout | keep `dev = [...]` as a table under `[dependency-groups]` |
| Tests hang | missing timeout | ALWAYS `timeout 130 uv run pytest --timeout=30 -q` |

## Health & metrics

`GET /api/v1/health` → `{"status": "ok", "pending": N}` where `pending` = queued scan jobs (in-memory). Wire it to your uptime monitor; it's public by design.

For deeper metrics, watch the DB directly:

```sql
-- scans by status
SELECT status, COUNT(*) FROM scans GROUP BY status;

-- findings per day (last 7 days)
SELECT substr(s.started_at,1,10) day, severity, COUNT(*)
FROM findings f JOIN scans s ON s.id=f.scan_id
GROUP BY day, severity ORDER BY day DESC;
```

## Reset a demo environment

```bash
rm data/festin.db
# restart → migrate() recreates schema → register the admin again
```

## Deployment checklist

1. `FESTIN_JWT_SECRET` set to a random value
2. demo DB deleted
3. admin bootstrapped with a strong password
4. TLS in front (proxy/ingress)
5. `/auth/login` rate-limited at the proxy
6. backups scheduled (`.backup` method)
7. `curl /api/v1/health` wired to monitoring