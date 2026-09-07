# Deploying the documentation

The docs are a static MkDocs Material site built from `docs/` into `site/`.
Three publication paths, from quickest to most automated.

## Local preview

```bash
uv sync --group docs
uv run mkdocs serve          # live-reload on http://localhost:8000
```

Any edit to `docs/**` or `mkdocs.yml` refreshes the browser instantly.

## Build a static bundle

```bash
uv run mkdocs build          # output: site/
```

`site/` is fully static — host it anywhere:

```bash
# any static server
python3 -m http.server 8901 --directory site/

# or copy to any web root
rsync -a site/ server:/var/www/festin-docs/
```

!!! note "The bundle is already gitignored"
    `site/` is in `.gitignore` — only sources (`docs/`, `mkdocs.yml`) are
    committed. Never edit `site/` by hand.

## Option 1 — GitHub Pages (recommended, zero cost)

The repo ships a ready workflow: [`.github/workflows/docs.yml`](https://github.com/cr0hn/festin/blob/master/.github/workflows/docs.yml).

**One-time setup:** repo → **Settings → Pages → Source: GitHub Actions**.

Then on every push to `master`:

```bash
git push origin master       # ← needs the owner's explicit go-ahead
```

The workflow builds with `uv` and publishes to `https://cr0hn.github.io/festin/`.

### The workflow

```yaml
name: docs
on:
  push:
    branches: [master]
    paths: ["docs/**", "mkdocs.yml", "pyproject.toml", "uv.lock"]
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: true
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --group docs
      - run: uv run mkdocs build
      - uses: actions/upload-pages-artifact@v3
        with: {path: site}
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: {name: github-pages, url: ${{ steps.deployment.outputs.page_url }}}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

## Option 2 — Any static host

| Host | Command |
|---|---|
| Netlify | `netlify deploy --dir=site --prod` |
| Vercel | `vercel --prod` (build: `uv run mkdocs build`, output: `site`) |
| Cloudflare Pages | build `uv run mkdocs build`, output `site` |
| nginx / Caddy | `cp -r site/* /var/www/festin/` |

## Option 3 — Self-hosted next to the dashboard

Serve the docs from the same box as the dashboard (e.g. `docs.example.com`):

```yaml
# docker-compose addition
  docs:
    image: nginx:alpine
    volumes:
      - ./site:/usr/share/nginx/html:ro
    ports:
      - "8901:80"
```

## Publishing rules

- Content lives in `docs/**` + `mkdocs.yml`; nav is defined manually in `mkdocs.yml`.
- Screenshots go in `docs/img/` — capture them from the real dashboard (headless, PNG).
- Legacy LLM-facing files (`PROJECT.md`, `RUNBOOK.md`, `DESIGN_DECISIONS.md`) are excluded from the site via `exclude_docs` but kept in the repo.
- After changing JS/CSS of the docs theme there's no cache-busting version —
  Material fingerprints its own assets; ours are tiny and unversioned.

## Adding a page

1. Create `docs/<section>/<page>.md`.
2. Add it to `nav:` in `mkdocs.yml` (nav is explicit, not inferred).
3. `uv run mkdocs serve` and check the link — `mkdocs build` fails CI on broken internal links.