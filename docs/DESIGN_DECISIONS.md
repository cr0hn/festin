# Decisiones de diseño y su rationale (ADR resumido)

> Registro de las decisiones técnicas importantes, por qué se tomaron y qué
> alternativas se descartaron. Para contexto completo de arquitectura, ver
> [PROJECT.md](PROJECT.md) Nivel 2.

---

## 1. aiohttp en el servicio (no FastAPI)

**Decisión:** el dashboard (`festin/service/serve.py`) usa aiohttp.

**Rationale:** el escáner original (`festin/api.py`) ya era aiohttp y todo el
runtime es asyncio puro. Migrar a FastAPI habría obligado a reescribir el
middleware de auth y el static serving por ganancia cero.

**Consecuencia:** los archivos `festin/service/api/` (routers FastAPI del
primer diseño) quedaron legacy sin wirear. Ver PROJECT.md Nivel 5, punto 3.

## 2. SQLite por defecto, asyncpg preparado pero no implementado

**Decisión:** `Database` (database.py) usa `aiosqlite` con una única
conexión. asyncpg está declarado en pyproject pero no hay código Postgres.

**Por qué:** para monitoreo de 1-10 dominios en un solo host, SQLite es
cero-config y suficiente. La clase encapsula el acceso para que migrar a
pool/Postgres sea tocar solo `connect()`/`_execute()`.

**Deuda:** sin pooling no hay multi-worker. Si se necesita, el sitio a tocar
está aislado.

## 3. JWT HS256 con secret de env, sin refresh tokens

**Decisión:** `python-jose`, HS256, 60 min, secret de `FESTIN_JWT_SECRET`
con fallback dev. Token en `localStorage`.

**Descartado:** sessions de servidor (requieren store compartido para
escalar horizontal), OAuth (no hay proveedor), refresh tokens (el SPA
simplemente re-loguea).

**Deuda conocida:** no hay rate-limit en login; HS256 es suficiente para
uso interno pero no para clientes externos.

## 4. Bootstrap de primer usuario en el handler, no en el middleware

**Decisión:** `JWTMiddleware` nunca exime `/auth/register`. Sin header →
pasa anónimo; con header → setea `request["user"]`. El handler decide:
`user_count()==0` → admin; si no, exige Bearer admin.

**Por qué:** eximir register en el middleware hacía que un admin con token
válido pareciera anónimo (`request["user"]` nunca se seteaba) y recibiera
401 — bug real de la sesión del 2026-09-07. La regla: *el middleware
identifica, el handler autoriza*.

## 5. Scan real en background task, no cola de trabajos distribuida

**Decisión:** `POST /scans/run-scan` crea el registro en BD y lanza
`asyncio.create_task(_execute_scan(...))` en el mismo proceso.

**Descartado:** cola Redis con workers separados. Para el volumen actual
(escasos scans manuales + programados) la task in-process es suficiente y
evita una dependencia operativa (Redis corriendo).

**Trampas evitadas:** la primera implementación devolvía `scan_id` fijo
("api-0") sin tocar la BD — un stub que fingía aceptar scans. Ahora el
scan_id devuelto es el rowid real y el estado se actualiza al terminar.

## 6. Contrato SPA-first para la API

**Decisión:** los endpoints y claves de respuesta (`scheduled` no
`schedules`) siguen lo que `static/js/app.js` ya consumía.

**Por qué:** el JS existente era la especificación de facto; alinear el
backend a ella evitó tocar el frontend. Si algún día hay clientes no-SPA,
documentar ambas claves o versionar la API.

## 7. Vanilla JS + CSS propio en la SPA (sin framework ni build)

**Decisión:** un solo `index.html`, un `app.js` (~440 líneas), un
`style.css`. Sin Tailwind, sin Alpine, sin bundler.

**Descartado:** React/Vue (build step, node_modules, inaccesible para
auditoría rápida), CDN de Tailwind (dependencia externa en una tool de
seguridad — mal mensaje).

**Coste asumido:** gestión manual del DOM y del estado de sesión. El bug
de `refreshAll`/`startSession` borradas durante una reescritura de `init()`
es exactamente el tipo de error que un framework habría evitado. La lección
está en PROJECT.md Nivel 3, punto 9.

## 8. Tracking in-memory de last_run en el scheduler

**Decisión:** `_scan_loop` guarda `last_run: dict[schedule_id, timestamp]`
en memoria, inicializado a 0.

**Consecuencia:** tras reinicio del proceso, todos los scheduled scans se
ejecutan en el primer ciclo. Aceptable para monitoreo (duplicar un scan es
inofensivo); si deja de serlo, la tabla `scheduled_scans` tendría que ganar
una columna `last_run_at`.

## 9. Eliminación de festinn/

**Decisión:** el directorio duplicado `festinn/` (copia fallida de una
sesión interrumpida, con `serve.py` stub de 6 líneas) se eliminó en
`1c889b7`. El canónico es `festin/service/` (referenciado por pyproject).

**Lección:** cuando una sesión muere a mitad de refactor, resolver el
duplicado *antes* de seguir desarrollando — cada fix aplicado sobre la copia
equivocada se pierde.

## 10. Suite de tests con timeout obligatorio

**Decisión:** todo comando pytest lleva `timeout 130` de shell +
`pytest --timeout=30`. Hay tests (serve/scheduler) que cuelgan indefinidamente
sin ellos.

**Por qué:** dos incidents con el suite bloqueado consumieron más tiempo que
el propio fix. Es disciplina de sesión, no preferencia.