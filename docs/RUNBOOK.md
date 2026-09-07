# Runbook — Operación y troubleshooting del dashboard

> Comandos operativos. Para contexto ver [PROJECT.md](PROJECT.md).

---

## Arrancar / parar

```bash
# Servidor (puerto 8420, BD en data/festin.db)
uv run python -m festin.service.serve

# Con flags
uv run python -m festin.service.serve --host 0.0.0.0 --port 8420 \
  --db /path/to/festin.db --auth-file users.txt   # users.txt: user:pass por línea (basic-auth legacy)

# Verificar
curl -s localhost:8420/api/v1/health
```

El CLI `festin serve` (typer) también levanta el dashboard: usa `--host`,
`--port`, `--db`.

## Credenciales

| Situación | Cómo |
|---|---|
| BD limpia (0 usuarios) | `POST /api/v1/auth/register` → primer user es admin |
| BD poblada | solo un admin existente puede crear usuarios (mismo endpoint con su Bearer) |
| Perdiste el admin | borra la tabla users de la BD (`DELETE FROM users`) y re-bootstrapea |

```bash
# Bootstrap manual
curl -X POST localhost:8420/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"CAMBIAME"}'
```

## Troubleshooting

| Síntoma | Causa probable | Fix |
|---|---|---|
| `401 unauthorized` en register con admin logueado | middleware eximiendo register (ver PROJECT.md N3) | comprobar que `DEFAULT_EXEMPT_PATHS` NO incluye `/auth/register` |
| Login falla pero curl funciona | SPA con `registerMode=true` residual o token viejo en localStorage | `localStorage.clear()` y recargar |
| Tests colgan | falta `--timeout=30` | siempre `pytest --timeout=30 -q` con shell timeout |
| `uv run` falla parseando pyproject | formato TOML de dependency-groups | debe ser `[dependency-groups]` + `dev = [...]` |
| "Response object is not callable" | middleware aiohttp old-style | usar patrón `_wrap_middleware()` de serve.py |
| passlib crash en bcrypt | passlib 1.7 vs bcrypt 4.x | auth.py usa bcrypt directo — no reinstalar passlib |
| Scan en cola nunca termina | `_execute_scan` task murió | revisar logs del proceso; el estado queda en `running` |

## Reset de demo (entorno local)

```bash
# Ojo: borra datos
rm data/festin.db
# reinicia el servicio y bootstrapea admin de nuevo
```

## Push

El repo tiene commits locales sin publicar (ver PROJECT.md Nivel 5).
El push está a la espera de confirmación explícita del dueño:

```bash
git push origin master
```