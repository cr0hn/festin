---
tipo: pitfall
fecha: 2026-09-08
symptom-when-to-open: A CI job publishing to PyPI fails with "400 File already exists" after a push that didn't change the version
---

# PyPI rejects same-version re-uploads — every code push that rebuilds the
# wheel needs a version bump

## Symptom

CI green through test + build, then the `publish` job fails:

```
ERROR    HTTPError: 400 Bad Request from https://upload.pypi.org/legacy/
<title>400 File already exists ('festin-0.4.0.tar.gz', ...
```

## Cause

The CI workflow publishes to PyPI on every push to `master` (when code
paths change). PyPI forbids re-uploading a file with the same name —
`festin-0.4.0.tar.gz` already being on the index means the upload is
rejected even if the content differs. Version reuse is not allowed
(https://pypi.org/help/#file-name-reuse).

## Contraste

Seen on three separate runs in FestIn (Sept 2026): doc-only pushes and
policy commits both rebuilt the wheel with an unchanged version and failed
at exactly this point. Once `version` was bumped in `pyproject.toml` +
`festin/__init__.py`, the publish succeeded immediately.

## How to avoid it

- The version lives in TWO places — bump both:
  `pyproject.toml` (`version = "..."`) and `festin/__init__.py`
  (`__version__ = "..."`).
- Policy (owner's rule): **patch bumps (0.4.X) autonomously; minor bumps
  only on the owner's explicit order.**
- CI is scoped with a `paths` filter (`festin/**`, `tests/**`,
  `pyproject.toml`, `uv.lock`, the workflow itself) so doc-only pushes
  don't even reach the publish step.