# FestIn — Guía del Proyecto (Progressive Disclosure)

> **Para IAs y desarrolladores que llegan en frío.** Lee en capas: cada sección
> asume la anterior. 30 segundos te dan el contexto mínimo; los detalles están
> al final solo si los necesitas.

---

## Nivel 0 — El proyecto en 30 segundos

**FestIn** es un buscador de buckets S3 expuestos + un dashboard de monitoreo.

- **CLI escáner** (`festin scan`): rastrea dominios, permuta nombres, sondea
  buckets S3 en AWS/GCP/Azure, detecta secretos. Es el proyecto original,
  estable y testado.
- **Servicio de monitoreo** (`festin-serve`): dashboard web multi-usuario que
  persiste scans en SQLite, programa rescans periódicos y expone REST API +
  SPA. **Este es el módulo nuevo** (`festin/service/`), completado en
  septiembre de 2026.

```
Comandos rápidos
────────────────
uv run pytest --timeout=30 -q     # suite completa (SIEMPRE con timeout)
uv run festin scan example.com    # escáner CLI
uv run python -m festin.service.serve   # dashboard en http://127.0.0.1:8420
```

**Estado actual:** 289 tests en verde, 8 commits locales sin push (pendiente
de confirmación del usuario). Demo corriendo con `admin/admin123`.

---

## Nivel 1 — Anatomía del repo (2 minutos)

```
festin/                  ← paquete escáner (no tocar sin leer Nivel 3)
├── cli.py               ← CLI typer: scan, serve, version
├── scan_runner.py       ← pipeline de escaneo (lo llama el servicio)
├── api.py               ← API REST antigua (aún usada por tests)
└── ...                  ← s3.py, secrets.py, permutations.py, etc.

festin/service/          ← dashboard de monitoreo (lo nuevo)
├── serve.py             ← factory aiohttp + middleware wiring + main()
├── router.py            ← FestinRouter: todos los endpoints REST
├── auth.py              ← bcrypt + JWT + JWTMiddleware + AuthMiddleware
├── database.py          ← aiosqlite (23 métodos CRUD)
├── scheduler.py         ← FestInScheduler + Scheduler facade
├── queues.py            ← QueueManager (in-memory + Redis-optional)
├── models.py            ← modelos compartidos
├── api/                 ← routers FastAPI antiguos (legacy, no wireados)
└── static/              ← SPA: index.html + js/app.js + css/style.css

tests/                   ← 289 tests; test_service.py cubre el dashboard
docs/                    ← estás aquí
```

**Punto crítico:** hay DOS servidores HTTP. `festin/api.py` (aiohttp, solo
lectura de state files, legacy) y `festin/service/serve.py` (el dashboard
completo). No los confundas.

---

## Nivel 2 — Cómo funciona el servicio (5 minutos)

### Arranque
`python -m festin.service.serve` → `create_app()` en `serve.py`:
1. Crea `Database`, `QueueManager`, `Scheduler(database=db)`
2. Monta JWT middleware (env `FESTIN_JWT_SECRET`, default dev)
3. Construye `FestinRouter(db, queues, scheduler, auth=AuthService(db, secret))`
4. Registra rutas en `/api/v1/*`, monta `/static`, sirve SPA en `/`
5. En startup: `db.connect()` + `db.migrate()`; en shutdown: `db.disconnect()`

### Autenticación (semántica exacta)
| Request | Resultado |
|---|---|
| `POST /auth/register` con BD de usuarios **vacía** | 201, rol **admin** (bootstrap) |
| `POST /auth/register` con Bearer de admin | 201, rol viewer |
| `POST /auth/register` con Bearer viewer | 403 |
| `POST /auth/register` anónimo con BD poblada | 401 |
| Cualquier otro endpoint sin Bearer válido | 401 |
| `GET /health` | público |

**Detalle sutil que rompió cosas antes:** el middleware JWT *no* exime
`/auth/register` — lo atraviesa siempre. Sin header → pasa anónimo (el
handler decide por `user_count`); con header → setea `request["user"]` para
que el handler sepa si es admin. Si lo eximes, el admin parece anónimo y
recibe 401 (bug que ya ocurrió — no lo reintroduzcas).

### Flujo de un scan
```
UI: POST /scans/run-scan {domains: [...]}
  → router._handle_run_scan:
      1. upsert domain (db.find_domain / create_domain)
      2. db.create_scan(domain_id)            ← registro en BD
      3. asyncio.create_task(_execute_scan)   ← background
         └─ festin.scan_runner.run_scan()     ← escaneo real
         └─ db.update_scan_status(...)        ← persiste resultado
  → 202 {scan_id, job_id, status: "accepted"}
```

### Scans programados
`_scan_loop` (scheduler.py) corre cada `check_interval` (10s default):
lee `scheduled_scans` de la BD, y cuando vence el intervalo de cada fila
(tracking in-memory `last_run`), dispara `trigger_scan` como task. **Nota:**
el tracking es en memoria — tras reinicio, el primer ciclo re-ejecuta todo.

### SPA (static/js/app.js — vanilla JS, sin build)
- Login → JWT en `localStorage["festin_token"]` → `apiFetch` añade Bearer
- 401 en cualquier fetch → limpia token, vuelve al login
- Poll cada 30s: `/stats`, `/scans`, `/findings`, `/buckets`,
  `/queues/schedule`, `/health`
- Modo registro: el link "Create admin account" alterna `registerMode` y
  cambia el texto del formulario

---

## Nivel 3 — Trampas conocidas (léete esto antes de tocar nada)

### Python / aiohttp
1. **`@web.middleware` en instancias no funciona en aiohttp 3.14.** El
   marker debe estar en la función envuelta. Por eso `serve.py` usa
   `_wrap_middleware(instance)` que crea una función nueva-style que llama
   `instance.__call__`. Si añades middleware nuevo, usa el mismo patrón.
2. **`JWTMiddleware.__middleware_version__ = 1`** en la clase — sin él
   aiohttp despacha old-style y crashea con "Response object is not
   callable". No lo borres.
3. **aiosqlite:** DDL multi-statement necesita `executescript()`, no
   `execute()`. `migrate()` ya lo usa.
4. **passlib 1.7 es incompatible con bcrypt 4.x** (crash en
   `detect_wrap_bug`). `auth.py` usa bcrypt directamente con truncado a 72
   bytes. No reinstales passlib.
5. **uv/pyproject:** `[dependency-groups.dev]` como tabla NO parsea — debe
   ser `[dependency-groups]` con `dev = [...]`. El build-backend correcto es
   `hatchling.build`, no `hatchling`.

### Tests
6. **SIEMPRE `timeout` + `pytest --timeout=30`.** Hay tests que cuelgan sin
   él (el suite completo son ~67s con el flag).
7. `tests/test_complexity.py` mantiene una lista `constant_only` de módulos
   sin funciones. Si añades un módulo nuevo a `festin/` solo con constantes,
   añádelo ahí o fallará.
8. Los tests del CLI pinchan el contrato de flags de `serve` (`--db`, no
   `--state`). Si cambias el CLI, actualiza `tests/test_cli.py`.

### SPA (JS)
9. **`node --check` NO basta.** Referenciar una función inexistente dentro
   de otra es runtime error, no sintaxis. Tras cirugía en app.js, verifica
   que cada función llamada tiene definición (ya mordimos con
   `startSession` y `refreshAll` — dos veces).
10. El backend devuelve la clave `scheduled` en `GET /queues/schedule` (no
    `schedules`) — contrato fijado por el JS existente.
11. Los `alert()` fueron sustituidos por `showFlash()` (toast) porque
    bloquean el browser headless y son mala UX. No reintroduzcas alerts.

---

## Nivel 4 — Referencia API completa

Todos los endpoints bajo `/api/v1` (salvo los marcados públicos):

| Método | Ruta | Función | Notas |
|---|---|---|---|
| GET | `/health` | público | `{"status":"ok","pending":N}` |
| POST | `/auth/login` | público | `{username,password}` → `{access_token}` JWT HS256 60min |
| POST | `/auth/register` | semi-público | bootstrap/admin según tabla |
| GET | `/stats` | auth | `{"scan_count","findings":{"total","critical","high"}}` |
| GET | `/scans?limit=` | auth | lista paginada |
| DELETE | `/scans/{id}` | auth | cascade findings/buckets |
| GET | `/findings?severity=&limit=` | auth | filtrable |
| GET | `/buckets?limit=` | auth | |
| GET/POST | `/queues/schedule` | auth | clave de respuesta: `scheduled` |
| DELETE | `/queues/schedule/{id}` | auth | |
| POST | `/scans/run-scan` | auth | 202, ejecuta scan real en background |

**Database (23 métodos):** ver `festin/service/database.py` — CRUD de
domains, scans, users, findings, buckets, scheduled_scans + `get_stats()` y
`get_dashboard_overview()` (este último legado del diseño FastAPI, no
wireado).

**Scheduler facade** (`Scheduler` en scheduler.py): `enqueue(domains)` →
`{"job_id","status":"pending"}`, `stats()` → `{"pending","running","completed"}`,
`complete`, `fail`. El `FestInScheduler` interno es el que corre el loop.

---

## Nivel 5 — Estado git y pendientes

**Commits locales (8, sin push):**
```
3c1add0 fix(spa): restore refreshAll dropped during init() rewrite
a197cb6 fix(spa): define startSession used by login/register handlers
c56a1a2 fix(service): admin user creation, scheduled scan execution, non-blocking UI
7c47961 feat(service): multi-user JWT auth, complete API, working scan pipeline
1c889b7 chore: remove festinn/ duplicate service directory
012d950 fix(service): make festin-serve and tests green end-to-end
9625736 feat(service): FestIn monitoring dashboard with FastAPI SPA + scheduler
c1dcf2c docs(readme): add Real-world validation section with live test results
```

**Pendientes (conocidos, por prioridad):**
1. **Push a origin** — bloqueado por decisión del usuario, no técnico.
2. **Escalabilidad real:** 1 conexión SQLite global; la cola es in-memory
   (Redis via coredis existe en `queues.py` pero sin wirear). asyncpg
   declarado en pyproject pero no implementado. Capacidad multi-worker:
   nula.
3. **`festin/service/api/` es legacy** (diseño FastAPI descartado). Los
   routers `domains.py`/`scans.py` no están wireados; `api/__init__.py`
   tiene `set_db`/`get_scheduler` globals. Candidato a borrado.
4. **`get_dashboard_overview()`** en database.py no tiene endpoint (el SPA
   usa `/stats`). Revisar si se elimina o se wirea.
5. **Rate-limit en auth:** no hay protección contra brute-force en
   `/auth/login`.
6. **Tests del flujo register:** la matriz de registro (bootstrap/admin/
   viewer/anon) está verificada manualmente pero no cubierta por tests
   automatizados. Sería el primer test a añadir.
7. **`data/festin.db` está en git** — es estado de demo. Considerar
   .gitignore + migración de datos.

**Credenciales demo** (solo entorno local): `admin/admin123`,
`viewer2/viewerpass123`. El password del admin es conocido porque se
registró manualmente en esta sesión; en producción el bootstrap imprime
una contraseña autogenerada si no se provee.

---

## Nivel 6 — Cómo verificar tu trabajo (checklist)

```bash
# 1. Suite completa (67s)
timeout 130 uv run pytest --timeout=30 -q     # debe decir: 289 passed

# 2. Sintaxis JS (insuficiente solo, ver punto 9)
node --check festin/service/static/js/app.js

# 3. Funciones JS propias referenciadas = definidas (regla del punto 9).
#    Filtra globals del navegador; los nombres que queden son funciones
#    propias del archivo que DEBEN tener `function <name>`.
node -e "const s=require('fs').readFileSync('festin/service/static/js/app.js','utf8');
const SKIP=new Set(['function','if','for','while','catch','return','typeof','new','fetch','Error','clearTimeout','setTimeout','Date','confirm','encodeURIComponent','URLSearchParams','String','parseInt','clearInterval','setInterval','atob','alert','Promise','JSON','document','window','localStorage','console','RegExp','Set','Math']);
const called=[...s.matchAll(/(?<![.\w])(\w+)\s*\(/g)].map(m=>m[1]).filter(f=>!SKIP.has(f));
const missing=[...new Set(called)].filter(f=>!new RegExp('function\\\\s+'+f+'\\\\b').test(s));
console.log(missing.length? 'MISSING: '+missing.join(', ') : 'all defined');"

# 4. Arrancar el servicio y smoke-test
uv run python -m festin.service.serve &
curl -s localhost:8420/api/v1/health          # {"status":"ok",...}
curl -s localhost:8420/ | head -3             # HTML del SPA

# 5. Flujo de auth completo
curl -s -X POST localhost:8420/api/v1/auth/register \
  -H 'Content-Type: application/json' -d '{"username":"t","password":"t123"}'
# (con BD limpia → 201 admin; con BD poblada → 401 sin token)
```

Verificación de UI en headless: abrir `http://127.0.0.1:8420`, limpiar
`localStorage`, login → dashboard debe mostrar `user-name` con el username
y las tarjetas de stats con datos.