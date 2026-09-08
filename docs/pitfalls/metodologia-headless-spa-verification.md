---
tipo: metodologia
fecha: 2026-09-08
symptom-when-to-open: Verifying SPA frontend changes where the bug only appears with a specific cached state, or when headless browser checks disagree with what the user sees
---

# Headless SPA verification: clean localStorage + fresh tab + raw page.screenshot

## Context

Three real FestIn incidents were caught (or initially mis-diagnosed) by
headless browser verification:

1. **Self-demotion lockout**: the Users table allowed demoting your own
   admin → instant 403s. UI had to disable the role select for the current
   user.
2. **Stale app.js served from cache**: a fixed bug appeared to persist
   across reloads; `fetch()` with `cache: 'no-store'` proved the server
   served fresh code — the browser was the stale layer.
3. **view-findings section missing** from index.html: SPA completely inert,
   zero console errors.

## Method

- Headless browser tab, viewport matching the bug report (or 390x844 +
  1428x806 for responsive checks).
- `localStorage.clear()` BEFORE testing login — residual tokens/roles make
  auth flows lie (a "fixed" login may just be an old token still working).
- Login via element handles + `page.keyboard.type` (raw Puppeteer); the
  higher-level tab helpers can silently skip form submit.
- For screenshots into docs: `page.screenshot({type: 'png'})` — the tab
  helper `tab.screenshot()` returns JPEG regardless of extension, which
  corrupts `.png` files.
- Console errors are NOT reliable: errors inside async handlers or late
  listeners never surface. When a route "does nothing", probe state directly:

  ```js
  // is init() actually bound? probe a delegated-click side effect
  document.querySelector('[data-action="logout"]').click();
  localStorage.getItem("festin_token")   // null → init ran
  ```

- A test that toggles the hash and waits: `await page.reload({waitUntil:
  'networkidle0'})` + 1-2s settle before asserting.

## Contraste

Every UI change in the 0.3/0.4 releases was verified this way (desktop
1428-1508px + mobile 390px). The self-demotion bug and the inert-SPA bug
were both caught only because the verification ran real flows against the
real server, not unit tests of the JS.