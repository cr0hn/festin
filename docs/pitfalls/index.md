# Pitfalls & lessons

Field-tested gotchas from building and operating FestIn. Each note answers:
*what broke, why, what was run to confirm it, and how to avoid it* — with the
file/line that proved it.

Open these when a symptom matches yours; every note lists the literal error
or behavior that leads there.

| Note | Open when |
|---|---|
| [aiohttp instance middleware doesn't dispatch](aiohttp-instance-middleware-does-not-dispatch.md) | handlers 401/500 on every request, middleware silently ignored, no visible error |
| [HTML edit drops a tag → SPA inert](html-edit-drops-tag-spa-inert.md) | SPA renders stale content, a route does nothing after an HTML refactor, or padding/margins vanish |
| [PyPI rejects same-version re-uploads](pypi-rejects-same-version-reuploads.md) | CI publish job fails with "400 File already exists" |
| [Decision: version policy](decision-version-policy-patch-only.md) | planning a release, or wondering why the published version is 0.4.x |
| [Method: parallel agents with git worktrees](metodologia-parallel-agents-with-worktrees.md) | running a multi-agent refactor over shared files |
| [Methodology: headless SPA verification](metodologia-headless-spa-verification.md) | verifying SPA frontend changes; bugs that only appear with cached state |

## Conventions

- Frontmatter: `tipo` (pitfall / decision / metodologia), `fecha`,
  `symptom-when-to-open` (the literal symptom that leads here).
- Written in English — all project content is.
- Duplicates first: `grep` this folder before adding a note; extend an
  existing one with a dated section instead of creating a twin.