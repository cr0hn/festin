---
tipo: pitfall
fecha: 2026-09-08
symptom-when-to-open: An SPA that renders stale content, or a route/view that does nothing after an HTML refactor — with zero console errors
---

# Editing index.html by hand: one lost tag makes the whole SPA inert, and
# stale browser caches mask it

## Symptom (two real incidents, both in FestIn)

1. `docs/../index.html` lost a closing `</section>` during a scripted edit.
   Every route change threw `TypeError: Cannot read properties of null
   (setting 'hidden')` inside the router's hide-all loop — but the error was
   swallowed by an async gap, so the console stayed clean and the SPA was
   completely inert (no view rendered, no navigation worked).

2. A `<main class="content">` wrapper was lost the same way: the page rendered
   but all padding/margins were gone. The CSS was blamed first — twice.

**The trap that made both worse:** browsers kept serving a cached
`index.html`/`app.js` from a previous (working) state, so local reproduction
was inconsistent and hard to pin down.

## Cause

Hand-editing HTML with line-anchored tools silently drops opening/closing
tags when a range is off by one. A missing element that JS queries with
`$(id)` then throws inside the router, killing every subsequent navigation.

## Contraste

```bash
# tag-balance check — catches the missing/opposite-tag class of bugs
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('festin/service/static/index.html').read())" && echo parsed
```

Run after EVERY edit to index.html. Also encoded as a repo rule
(`docs/reference/development.md`, "HTML edits").

## How to avoid it

- Validate tag balance after any HTML edit (command above).
- Cache-bust the SPA shell: `serve.py` sends `Cache-Control: no-store` for
  `index.html` and `no-cache` for `/static/**`; JS/CSS are referenced with a
  `?v=N` query that must be bumped on every JS/CSS change. Without `?v=N`,
  stale cached JS produced phantom bugs that vanished on hard reload.