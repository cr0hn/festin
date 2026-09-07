# Quickstart

Two minutes from zero to your first findings.

## 1. Scan a domain

```bash
uvx festin scan example.com
```

Output:

```
[*] Scanning example.com ...
[+] Bucket found: example.com-backups (AWS, 412 objects)
[!] SECRET  AWS Access Key ID  backup.sql:112  AKIA****EXAMPLE
...
```

The scanner:

1. Crawls `example.com` following links (recursion 3 by default).
2. Resolves DNS CNAMEs pointing to S3-compatible storage.
3. Probes bucket names derived from the domain (and, with `--permute`, hundreds of permutations).
4. Downloads objects and matches them against secret-detection rules.

## 2. Get a report

```bash
# SARIF for GitHub Security / CI gates
uvx festin scan example.com --export sarif --output findings.sarif

# CSV for spreadsheets
uvx festin scan example.com --export csv --output findings.csv

# JSONL for log pipelines
uvx festin scan example.com --export jsonl --output findings.jsonl
```

## 3. Start the dashboard

```bash
uvx festin-serve --port 8420
```

Open `http://127.0.0.1:8420`:

1. The first account you register **becomes the admin** (bootstrap).
2. Create a project, add domains to it.
3. Hit **RUN SCAN (ALL DOMAINS)** — the service runs the same scanner engine as the CLI and persists every bucket and finding.

![Dashboard home](../img/dashboard-home.png)

## 4. Schedule continuous monitoring

Inside any project:

- **+ SCHEDULE** — rescan a domain every N minutes.
- The scheduler loop (10s tick) fires due scans automatically; results appear with `[DONE]` / `[FAIL]` status tokens.

## What next?

- [Scanner CLI reference](../usage/scanner.md) — every flag explained with examples.
- [Dashboard tour](../usage/dashboard.md) — views, roles, workflows.
- [REST API](../usage/api.md) — automate everything with `curl`.
- [Production deployment](../deploy/docker.md) — Docker, Kubernetes, HA.