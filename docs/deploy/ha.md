# High availability

What HA means for FestIn, what works today, and the supported paths to scale.

## Current state of the world

<span class="chip chip-warn">STATE</span> The service is designed as a **single-node, single-process** deployment:

| Component | Today | Constraint |
|---|---|---|
| Database | SQLite, **one global connection** | no multi-process sharing; single writer |
| Scan queue | in-memory (`asyncio` tasks) | jobs die with the pod |
| Scheduler | in-process loop (10 s tick) | `last_run` is memory state |
| Auth | JWT HS256 — stateless | ![ok][ok] survives restarts, works across replicas |
| UI/API | stateless handlers | ![ok][ok] horizontally scalable *for reads* |

What this means operationally:

- **One dashboard instance** attached to one SQLite file.
- Pod restart = in-flight scans lost (status rows stay `running`), schedules re-fire on the first tick.
- Kubernetes `strategy: Recreate` + single replica is the honest topology.

## Supported HA patterns

### Pattern 1 — Active/passive with fast failover <span class="chip chip-ok">WORKS TODAY</span>

The pragmatic 99.9% solution:

```
            ┌────────────┐
ingress ───▶│   proxy    │───▶ festin-0 (active)
            │ (k8s svc)  │      └── RWO volume (SQLite)
            └────────────┘
                  │ on node failure → reschedule
                  ▼
            festin pod moves to another node, remounts volume
```

- Kubernetes already gives you this: the pod reschedules, the PVC re-attaches, SQLite is consistent.
- Keep `replicas: 1`, `strategy: Recreate`, `readinessProbe` on `/api/v1/health`.
- RTO = pod reschedule time (seconds–minutes). RPO = last SQLite backup.
- Add a CronJob backup every N minutes → RPO bounded by backup cadence.

**This is the recommended setup** until SQLite becomes the bottleneck.

### Pattern 2 — Read replicas of the UI <span class="chip chip-ok">WORKS TODAY</span>

Auth (JWT) is stateless and handlers are read-mostly:

- Run **N dashboard replicas** that serve UI + API reads.
- All *mutations* and the scheduler live in **one** active instance.
- SQLite: replicate via Litestream (S3 tail) or SQLite WAL streaming; replicas restore a read-only copy.

Caveat: replicas must not run the scheduler or accept mutations → small patch: disable the scheduler + run behind a proxy that routes `POST/DELETE/PATCH` to the active instance. Worth it only if read traffic actually matters.

### Pattern 3 — PostgreSQL backend <span class="chip chip-info">ROADMAP</span>

The real unlock for multi-replica:

```text
[ingress] → N × (API + UI pods)  →  PostgreSQL (primary + replica)
                 ↓
           scan workers (Deployment, consumes a real queue)
```

- `asyncpg` is already a dependency; the `Database` class isolates the change surface (`connect()`/query layer).
- Queue in Redis (the `queues.py` Redis path exists but is unwired).
- Then: HPA on replicas, `RollingUpdate`, per-day findings in a real DB.

Tracked as a design goal in [ADR #2](../reference/design-decisions.md).

### Pattern 4 — Splitting the scanner <span class="chip chip-info">RECOMMENDED FOR SCALE</span>

The scanner is the heavy part. Scale it independently of the UI:

- Keep 1 dashboard replica (UI + state).
- Run scans as **Kubernetes CronJobs / external workers** calling `POST /scans/run-scan` or writing via the API.
- The service already treats scans as async tasks — replacing the in-process executor with a queue consumer is the one integration point (see [architecture](../reference/architecture.md#scaling-story)).

## Anti-patterns (do not do)

| Anti-pattern | Why it breaks |
|---|---|
| `replicas: 2` with the same RWO SQLite volume | second pod can't mount the volume / corrupts on RWX |
| Rolling update (`RollingUpdate`) | old+new pod overlap on one SQLite file |
| Two schedulers on one schedule table | double scans (no distributed lock) |
| Backing up with `cp` on a live DB | mid-write corruption; use `.backup` |

## Decision matrix

| Need | Choose |
|---|---|
| "Server reboots without losing data" | Pattern 1 (already have it) |
| "Read traffic is high, writes are few" | Pattern 2 (read replicas) |
| "Multiple writers / true HA" | Pattern 3 (Postgres + queue) — requires dev work |
| "More scanning throughput" | Pattern 4 (external workers) — works today |

[ok]: https://img.shields.io/badge/-ok-7bd88f "ok"