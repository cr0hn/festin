# Development

Everything you need to hack on FestIn safely.

## Setup

```bash
git clone https://github.com/cr0hn/festin.git
cd festin
uv sync --group dev
```

## Commands

```bash
# full test suite — ALWAYS double timeout (some tests hang without it)
timeout 130 uv run pytest --timeout=30 -q          # → 305 passed

# single file
timeout 60 uv run pytest tests/test_service.py --timeout=30 -q

# lint
uv run ruff check festin/

# complexity gate (CI enforces ≤ 10 per function)
uv run python -c "from radon.complexity import cc_visit; from pathlib import Path; \
  print([(b.name, b.complexity) for f in ['database','router','scheduler'] \
  for b in cc_visit(Path(f'festin/service/{f}.py').read_text()) if b.complexity > 10])"

# docs preview
uv run mkdocs serve
```

## Rules of the repo

1. **Tests always with double timeout.** Some tests hang indefinitely otherwise — this bit twice.
2. **Never `git push`** without explicit instruction from the owner.
3. **JS after surgery**: `node --check` only catches syntax. Verify every *called* function is *defined* (this caused two real bugs: `startSession`, `refreshAll`):

    ```bash
    node -e "const s=require('fs').readFileSync('festin/service/static/js/app.js','utf8');
    const SKIP=new Set(['function','if','for','while','catch','return','typeof','new','fetch','Error','clearTimeout','setTimeout','Date','encodeURIComponent','URLSearchParams','String','parseInt','clearInterval','setInterval','atob','Promise','JSON','document','window','localStorage','console','RegExp','Set','Math']);
    const called=[...s.matchAll(/(?<![.\w])(\w+)\s*\(/g)].map(m=>m[1]).filter(f=>!SKIP.has(f));
    const missing=[...new Set(called)].filter(f=>!new RegExp('function\\\\s+'+f+'\\\\b').test(s));
    console.log(missing.length? 'MISSING: '+missing.join(', ') : 'all defined');"
    ```

4. **Frozen contracts** — breaking them is a regression:
    - `GET /queues/schedule` returns the key **`scheduled`** (not `schedules`)
    - `POST /auth/register` is **never exempt** in the JWT middleware (middleware identifies, handler authorizes)
    - `serve` module CLI uses `--db` (not `--state`)
    - findings/buckets are persisted as **rows**, not just counters

5. **VERSION POLICY — never bump the minor without the owner's explicit order.** Patch bumps only (0.4.0 → 0.4.1) autonomously when a publish is needed. Minor decisions (0.4.0 → 0.5.0) belong to Daniel.

6. **Static assets are cache-busted with `?v=N`** in `index.html` — bump the version on every JS/CSS change, or browsers serve stale copies.
7. **HTML edits**: validate tag balance after touching `index.html` (a lost tag once made the whole SPA inert):

    ```bash
    python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('festin/service/static/index.html').read())" && echo parsed
    ```

8. **UI verification** is done with a headless browser against the real server (`localhost:8420`) with `localStorage.clear()` first — residual state lies.

## Testing conventions

- pytest-asyncio in `auto` mode; async fixtures allowed.
- Tests for the service live in `tests/test_service.py` (core) and `tests/test_service_projects.py` (projects/persistence/users/stats).
- Complexity gate: every function ≤ 10 (radon) — `tests/test_complexity.py` fails CI otherwise. Refactor, don't fight it.
- New constants-only module? Add it to `constant_only` in `tests/test_complexity.py`.

## Project layout

```
festin/               scanner core
festin/service/       dashboard (aiohttp)
festin/service/static SPA (vanilla JS)
tests/                305 tests
docs/                 MkDocs Material (this site)
```

## Documentation

```bash
uv run mkdocs serve        # live preview on :8000
uv run mkdocs build        # static site in site/
```

Screenshots for the docs are taken from the real dashboard (headless browser, PNG); keep them in `docs/img/`.

## Commit convention

`type(scope): summary` — e.g. `fix(spa): restore content wrapper`, `feat(service): multi-project schema`.