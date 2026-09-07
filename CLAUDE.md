# CLAUDE.md — FestIn

Guía de arranque para LLMs. **Lee en capas**: si tu tarea es pequeña, los
niveles 0-2 bastan. Nivel 3+ solo cuando vayas a tocar código.

---

## Nivel 0 — Identidad (10 segundos)

**FestIn**: buscador de buckets S3 expuestos (CLI) + dashboard web de
monitoreo multi-usuario (servicio). Python 3.13+, uv, pytest.

- Escáner CLI: `festin/` — estable, maduro, no tocar sin necesidad.
- Dashboard: `festin/service/` — nuevo (sept 2026), aiohttp + SQLite + JWT.
- **289 tests en verde. 9 commits locales SIN push (bloqueado por el usuario — nunca push sin orden explícita).**

```bash
uv run pytest --timeout=30 -q     # suite completa (~67s). timeout SIEMPRE
uv run python -m festin.service.serve   # dashboard en :8420 (admin/admin123 demo)
```

---

## Nivel 1 — Mapa (30 segundos)

```
festin/                → escáner CLI (cli.py = typer; scan_runner.py = pipeline)
festin/service/        → dashboard: serve.py (factory), router.py (API),
                         auth.py (JWT/bcrypt), database.py (23 métodos),
                         scheduler.py, queues.py, static/ (SPA vanilla JS)
tests/                 → 21 archivos; test_service.py = dashboard
docs/                  → PROJECT.md (guía profunda), DESIGN_DECISIONS.md, RUNBOOK.md
```

**Trampa de mapa:** existen DOS servidores HTTP. `festin/api.py` es legacy
(solo lectura de state files). El vivo es `festin/service/serve.py`. Y
`festin/service/api/` son routers FastAPI huérfanos nunca wireados.

---

## Nivel 2 — Reglas de sesión (obligatorias)

1. **Tests siempre con timeout doble**: `timeout 130 uv run pytest --timeout=30 -q`.
   Hay tests que cuelgan indefinidamente sin él. Ya pasó dos veces.
2. **NUNCA `git push`** sin orden explícita del usuario.
3. **JS tras cirugía**: `node --check` es insuficiente (errores runtime no
   son sintaxis). Verifica que toda función llamada está definida:
   `grep -o 'nombreFuncion(' festin/service/static/js/app.js | head` vs su
   `function nombreFuncion(`. Ya costó dos bugs (`startSession`, `refreshAll`).
4. **Contratos congelados** (romperlos = regresión):
   - `GET /queues/schedule` devuelve clave `scheduled` (no `schedules`)
   - `POST /auth/register` NO está exento en el middleware JWT (el admin
     debe poder autenticarse ahí; bootstrap anónimo pasa por la rama sin header)
   - `serve` CLI usa `--db` (no `--state`)
5. **Verificación de UI**: siempre headless browser contra el servidor real
   (`localhost:8420`). `localStorage.clear()` antes de testear login — el
   estado residual engaña.

---

## Nivel 3 — Detalles del servicio (solo si vas a tocar `festin/service/`)

### Auth — semántica exacta
| Request | Resultado |
|---|---|
| register + BD vacía (sin token) | 201 admin (bootstrap) |
| register + Bearer admin | 201 viewer |
| register + Bearer viewer | 403 |
| register anónimo + BD poblada | 401 |
| resto de endpoints sin Bearer válido | 401 (`/health` público) |

El middleware JWT *identifica*, el handler *autoriza*. No eximas register
en el middleware — un admin con token parecería anónimo y recibiría 401
(bug histórico).

### Flujo de scan
`POST /scans/run-scan` → upsert domain → `create_scan` (BD) → background
task ejecuta `festin.scan_runner.run_scan` → `update_scan_status`. El
scan_id devuelto es el rowid real.

### Scheduler
`_scan_loop` corre cada 10s, ejecuta scheduled_scans vencidos (tracking
in-memory `last_run` — tras reinicio re-ejecuta todo el primer ciclo).

---

## Nivel 4 — Trampas técnicas (léelas ANTES de editar)

| Área | Trampa |
|---|---|
| aiohttp 3.14 | `@web.middleware` en instancias no despacha new-style → usar patrón `_wrap_middleware()` de serve.py; `JWTMiddleware.__middleware_version__ = 1` no se toca |
| auth | passlib 1.7 crashea con bcrypt 4.x → auth.py usa bcrypt directo (truncado 72B). No reinstalar passlib |
| aiosqlite | DDL multi-statement = `executescript()` (ya en `migrate()`) |
| pyproject | `[dependency-groups.dev]` como tabla NO parsea en uv → `[dependency-groups]` con `dev = [...]`; backend = `hatchling.build` |
| tests | módulo nuevo solo-constantes → añadir a `constant_only` en tests/test_complexity.py |
| CLI tests | pinchan flags del modelo typer (`--db`); cambiar CLI implica actualizar tests/test_cli.py |
| SPA | sin alert() (bloquean headless) → usar `showFlash()`; clave de respuesta alineada con app.js |

---

## Nivel 5 — Dónde profundizar

| Necesitas | Ve a |
|---|---|
| Arquitectura completa, flujo de auth, API referencia (12 endpoints) | `docs/PROJECT.md` (7 niveles) |
| Por qué cada decisión técnica (con alternativas descartadas) | `docs/DESIGN_DECISIONS.md` |
| Operación, credenciales, troubleshooting | `docs/RUNBOOK.md` |
| Estado git + TODOs priorizados (push, escalabilidad, legacy api/) | `docs/PROJECT.md` Nivel 5 |

## Nivel 6 — Checklist de entrega

```bash
timeout 130 uv run pytest --timeout=30 -q        # → 289 passed
node --check festin/service/static/js/app.js     # + verificación manual de definiciones (regla 3)
uv run python -m festin.service.serve &          # smoke: /api/v1/health → {"status":"ok"}
# Si tocaste auth: curl register/login con la matriz del Nivel 3
# Si tocaste UI: headless browser con localStorage limpio
```

Commit con mensaje descriptivo (convención del repo: `type(scope): summary`
— ver `git log`). Sin push.