---
tipo: decision
fecha: 2026-09-08
symptom-when-to-open: When planning releases, or wondering why the published version is 0.4.x and not 0.5/1.0
---

# Version policy: patch bumps autonomous, minor bumps only on the owner's order

## Decision

FestIn stays in **0.3.X / 0.4.X patch bumps** for releases. The minor
number (0.4.0 → 0.5.0) is reserved for the owner's explicit decision — an
agent must never raise it on its own.

## Why

Stated by the owner (Daniel, 2026-09-08) after 0.4.0 shipped. Version
numbers are a product decision, not an implementation detail; an agent
incrementing minors on its own would misrepresent the project's maturity
to consumers.

## Enforcement

- Codified in `CLAUDE.md` (LLM onboarding, rule 3) and
  `docs/reference/development.md` rule 5 (human contributors).
- PyPI publishes require a patch bump each time: PyPI rejects re-uploads of
  the same filename (see the corresponding pitfall in `docs/pitfalls/`).
- Current line: 0.4.X.