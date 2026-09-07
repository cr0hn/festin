# FestIn

<p align="center">
  <img src="img/festin-logo-banner.png" alt="FestIn" width="140">
</p>

**FestIn** — **credentialless discovery and monitoring of exposed S3-compatible cloud storage from domains, DNS and web crawling.**

Two tools in one package:

| Component | What it does | Entry point |
|---|---|---|
| **Scanner CLI** | Crawls domains, permutes bucket names, probes S3-compatible buckets across cloud providers, detects secrets and exposed data. Battle-tested, single binary behavior. | `festin scan <domain>` |
| **Monitoring dashboard** | Multi-user, multi-project web service that schedules scans, persists results (buckets + findings), and renders an ops-console UI with exposure trends. | `festin serve` → `http://localhost:8420` |

---

## See it in action

**HOME — the exposure radar.** Headline trend, severity split, scan outcomes, rankings.

![Dashboard — home](img/dashboard-home.png)

**SCANS** — every execution with status tokens and project/status filters:

![Dashboard — scans](img/dashboard-scans.png)

**FINDINGS** — secrets classified by severity, with rule, bucket, object and redacted match:

![Dashboard — findings](img/dashboard-findings.png)

**Scan detail** — buckets with object counts, findings with line and redacted evidence:

![Scan detail](img/dashboard-project.png)

## Why FestIn

- **One tool, two missions.** Point it at a domain and it finds exposed S3 buckets. Or run the service and continuously watch entire project portfolios.
- **Built for ops.** The dashboard is an industrial ops console — monospace, dense, dark, no fluff — designed to be read at a glance in a SOC or terminal-adjacent workflow.
- **Async to the core.** Python 3.13+, asyncio end-to-end (aiohttp + aiosqlite), rate-limited probing, Tor support, checkpoint/resume for long scans.
- **Reports that matter.** SARIF / CSV / JSONL export for CI pipelines, plus a live REST API.

## Quick taste

=== "Scanner CLI"

    ```bash
    uvx festin scan example.com --permute --export sarif --output findings.sarif
    ```

=== "Dashboard"

    ```bash
    uvx festin-serve --port 8420
    # open http://127.0.0.1:8420 — first registered user becomes admin
    ```

=== "Docker"

    ```bash
    docker run -p 8420:8420 -v festin-data:/data ghcr.io/cr0hn/festin:latest serve --host 0.0.0.0 --db /data/festin.db
    ```

## What you get in the dashboard

- **Multi-project**: organize domains into projects, per-project counts and rankings.
- **Multi-user**: JWT auth, admin/viewer roles, user management UI.
- **Live results**: scan status ([QUEUED]/[RUNNING]/[DONE]/[FAIL]), findings with severity, buckets with object counts.
- **Exposure trend**: 14-day headline chart — is your exposure going up or down?
- **Scheduled scans**: periodic rescans per domain.

## Repository map

```
festin/            scanner core (CLI, pipeline, exports)
festin/service/    aiohttp dashboard (API + SPA + scheduler)
docs/              this documentation (MkDocs Material)
tests/             305 tests
```

## Next steps

<div class="grid cards" markdown>
- **[Installation](getting-started/installation.md)** — uv, pip or Docker.
- **[Quickstart](getting-started/quickstart.md)** — first scan in 2 minutes.
- **[Dashboard](usage/dashboard.md)** — every view explained.
- **[Deploy](deploy/docker.md)** — Docker, Kubernetes and HA patterns.
</div>

## License

Free and open for **personal and professional use** — no cost, no restrictions
on what you use it for.

If you want to build a **paid service or commercial product around FestIn**
(SaaS, managed offering, redistribution as part of a paid platform...), that
use case is **paid** and requires a separate agreement with the author first.
Contact: [cr0hn@cr0hn.com](mailto:cr0hn@cr0hn.com).

See [LICENSE](https://github.com/cr0hn/festin/blob/master/LICENSE) for the
full terms.