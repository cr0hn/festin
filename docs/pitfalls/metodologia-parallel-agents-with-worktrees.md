---
tipo: metodologia
fecha: 2026-09-08
symptom-when-to-open: Debugging a distributed/multi-agent refactor where local tests pass but shared files seem corrupted or behave inconsistently
---

# Parallel agent integration via git worktrees + branch-per-agent

## Context

A 10-item backlog was executed with 3 parallel coding agents editing
overlapping files (serve.py was touched by two, database.py was a full
rewritten by one). Direct co-editing in one tree caused mid-flight
breakages: a partially-refactored `Database.__new__` broke every test that
constructed a Database; two agents wrote conflicting `--help` text.

## Method (what worked)

1. **Worktree per agent**, branch per agent, all from the same base commit:

   ```bash
   git worktree add ../festin-queues  && git worktree add ../festin-postgres
   ```

2. Each agent works ONLY inside its worktree and **commits to its branch**
   (never pushes). File ownership declared up-front; overlaps resolved at
   integration.

3. **Main tree stays at the base commit** — stash any mid-flight changes
   (`git stash push -m "..."`) so agent worktrees never collide with
   integration state.

4. Agents own their files: conflicts are resolved by taking the branch side
   wholesale for agent-owned files; shared files (serve.py) get manual
   reconciliation — but keep those overlaps additive and isolated
   (e.g. middleware lists, config fields).

5. Integrate by merging branches into master, then run the full suite +
   radon + ruff. One integration pass, not N.

## Contraste

Used for the 0.4.0 release: RateLimit (serve.py), Postgres (database.py
rewrite, 948 lines), Queue (queues.py rewrite) — all three landed and the
merged suite went green on the first full run after conflict resolution.

## Gotchas

- Agents may produce **hidden drawer copies** in tests (e.g. pytest +
  aiohttp render hidden nav copies) — measure only visible elements
  (`offsetParent !== null`) or you chase phantom positioning bugs.
- An agent's mid-flight commit landed on master once; fixed by
  `git reset --hard <base>` in the main tree + fast-forwarding the agent
  branch. Keep the main tree clean and this is recoverable.
- Untracked test files created in the main tree must be physically moved
  into the agent's worktree (`git stash` does not carry untracked files).