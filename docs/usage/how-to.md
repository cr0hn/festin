# How-to guides

Task-oriented recipes. Each one is self-contained — pick the task, run the commands.

## Scanning

### Scan a large domain list without melting the box

```bash
# profile presets cap concurrency for you
uvx festin scan -f domains.txt --profile deep --checkpoint run.ckpt --quiet
```

- `deep` — thorough rate (default concurrency 5, longer timeouts)
- `fast` — aggressive probing for well-known infra
- `stealth` — slow, blends in, Tor-friendly

Add `--result-file buckets.jsonl` to stream results as they land; a 10k-domain run stays inspectable while running:

```bash
tail -f buckets.jsonl | jq 'select(.cloud_provider == "AWS")'
```

### Find only buckets, skip secret analysis

The crawler + prober do discovery; secret rules need object download. The fastest discovery-only pass:

```bash
uvx festin scan example.com --no-links --cloud --quiet --no-print
```

### Compare two points in time

```bash
uvx festin scan example.com --state run1.json --quiet
# ... a week later ...
uvx festin scan example.com --state run2.json --quiet
```

`--state` persists the complete `ScanResult`. Any tool that reads JSON can diff the two bucket sets.

### Verify a suspected leaked bucket

```bash
uvx festin scan example.com --permute --wordlist suspected-names.txt --result-file hits.jsonl
jq 'select(.bucket_name | contains("suspected"))' hits.jsonl
```

## Dashboard operations

### Onboard a new client (project)

1. **PROJECTS** → `+ NEW PROJECT` (`client-acme`).
2. Open it → **+ ADD DOMAIN** for each in-scope host.
3. **RUN SCAN (ALL DOMAINS)** for the baseline.
4. **+ SCHEDULE** on the important domains (e.g. 1440 min = daily).
5. Share a `viewer` account (USERS → `+ CREATE USER`).

### Rotate the admin password

1. Log in as admin → **USERS** → `+ CREATE USER` (role `admin`).
2. Log in with the new admin → delete the old one (you can't delete yourself).
3. Or straight SQL: `UPDATE users SET password_hash = <new bcrypt>;`

### Recover when the JWT secret was leaked

1. Stop the service.
2. Generate a new secret and update `FESTIN_JWT_SECRET`.
3. Start the service — **all outstanding tokens are instantly invalid**
   (they were signed with the old secret).

### Move the database to another machine

```bash
# safe copy (source machine, while running)
sqlite3 /data/festin.db ".backup '/tmp/festin-copy.db'"
scp /tmp/festin-copy.db newhost:/data/festin.db

# new machine
FESTIN_JWT_SECRET=<same-or-new> festin serve --db /data/festin.db
```

!!! note "Secret and tokens"
    If you move the DB to a host with a different `FESTIN_JWT_SECRET`, users
    simply log in again. Projects/scans/findings are unaffected.

### Run the scanner in CI on every push

```bash
uvx festin scan example.com --quiet --no-print --export sarif --output findings.sarif
# upload to GitHub code scanning with the SARIF upload action
```

### Back up on a schedule (cron)

```bash
# /etc/cron.d/festin-backup — every 6h, keep 28
0 */6 * * * festin sqlite3 /data/festin.db ".backup '/backups/festin-$(date +\%F-\%H).db'" \
  && find /backups -name 'festin-*.db' -mtime +7 -delete
```

### Feed the dashboard from the CLI

Bulk recon with the CLI, triage in the dashboard. For scheduled scanning see
the [Kubernetes CronJob](../deploy/kubernetes.md#scanner-as-a-cronjob) recipe.

```bash
# 1. sweep a large list
uvx festin scan -f all-targets.txt --quiet --no-print --export jsonl -o recon.jsonl

# 2. extract live domains
jq -r '.domain' recon.jsonl | sort -u > live.txt

# 3. load them into a project via the API
TOKEN=$(curl -s -X POST localhost:8420/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}' | jq -r .access_token)
while read -r d; do
  curl -s -X POST localhost:8420/api/v1/projects/2/domains \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"domain_name\": \"$d\"}"
done < live.txt
```

## Deployment

### Run behind Caddy with automatic TLS

```caddyfile
festin.example.com {
    reverse_proxy 127.0.0.1:8420
    # brute-force guard on login
    @login path /api/v1/auth/login
    rate_limit @login { zone login 5r/m }
}
```

### Run the scanner in CI on every schedule

See the [Kubernetes CronJob](../deploy/kubernetes.md#scanner-as-a-cronjob) manifest, or plain cron:

```cron
0 6 * * 1  cd /srv/festin && uvx festin scan -f targets.txt \
             --export sarif --output /var/reports/weekly.sarif --quiet
```

### Upgrade the dashboard with zero data loss

```bash
# docker compose
docker compose pull && docker compose up -d        # migrations are idempotent

# verify
curl -s localhost:8420/api/v1/health
curl -s localhost:8420/api/v1/stats -H "Authorization: Bearer $TOKEN" | jq .scan_count
```

### Contribute documentation

```bash
uv sync --group docs
uv run mkdocs serve          # live reload on :8000
```

Screenshots in `docs/img/` come from the real dashboard — regenerate them with a headless browser after UI changes.