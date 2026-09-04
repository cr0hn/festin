<p align="center">
  <img src="https://raw.githubusercontent.com/cr0hn/festin/master/images/festin-logo-banner.png" alt="Festin logo" width="600">
</p>

<h1 align="center">FestIn</h1>

<p align="center">
  <strong>The powered S3 bucket finder and content discover</strong>
</p>

<p align="center">
  <a href="#development"><img src="https://img.shields.io/badge/CI-GitHub_Actions-blue" alt="CI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.13%2B-blue" alt="Python 3.13+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-BSD--3--Clause-green" alt="License: BSD-3-Clause"></a>
  <a href="https://docs.astral.sh/ruff/"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
</p>

---

## What is FestIn

FestIn discovers **publicly exposed S3 buckets** starting from one or more domains.
It combines three discovery engines that feed each other recursively:

- **HTTP crawler** — fetches the site, follows links (bounded by a regex and a recursion limit) and recognizes S3-style XML listings.
- **DNS discovery** — resolves CNAMEs and enqueues every new target.
- **S3 probing** — tests bucket listing endpoints, follows S3 redirections and parses object listings.

No AWS credentials are required, and every probe is read-only.

## Why FestIn

| Feature | Description |
| --- | --- |
| Multi-engine recursion | Domains found by any engine feed the other two, up to a configurable depth. |
| Multi-cloud probing | One candidate bucket name is tested against AWS, Azure, GCS, DigitalOcean Spaces and Backblaze B2. |
| Bucket-name permutations | Expands each domain into candidate bucket names (env suffixes, wordlist merging). |
| Secret detection | Downloads text objects from found buckets and reports credentials (AWS keys, JWTs, private keys, tokens...) with severity ratings. |
| Scan state & diffing | Persists every scan to a JSON state file and diffs a new scan against the previous one. |
| Checkpoint / resume | Long scans are checkpointed per domain; an interrupted scan resumes where it stopped. |
| Rate profiles | `fast` / `deep` / `stealth` presets with adaptive backoff on 429/403/5xx responses. |
| Watch mode | Keeps running and picks up new domains appended to a file in real time. |
| REST API | Serve scan state over HTTP for dashboards and integrations. |
| Structured exports | CSV, SARIF 2.1.0 and JSONL output for SIEM/CI integration. |
| Tor support | Route all probes through a local Tor SOCKS5 proxy. |
| Filtering | Domain regex, black/white lists and a built-in CDN/social blacklist. |

## Install

Requires **Python 3.13 or newer**.

### uv (recommended)

```bash
uv tool install festin
festin --help
```

From a source checkout:

```bash
uv sync
uv run festin --help
```

### pip

```bash
pip install festin
festin --help
```

### Docker

```bash
docker run --rm -it cr0hn/festin --help
```

## Quick start

```bash
# Scan a single domain
festin scan example.com

# Bare invocation works too: scan is the default command
festin example.com

# Scan every domain in a file with quiet output and a strict crawl scope
festin scan -q -f domains.txt -dr '.example\.com.'

# Save everything: state file, streaming results, exports
festin scan -q example.com \
  --state state.json \
  -rr results.festin \
  --export sarif -o findings.sarif
```

## User guide

### Basic scan

```bash
festin scan mydomain.com
festin scan -f domains.txt          # domains from a file, one per line
```

Concurrent probes are bounded by `-c/--concurrency` (default 5).

### Watch mode

FestIn monitors the domains file and enqueues any domain appended to it,
forever — useful when piping output from dnsrecon, amass or cron jobs:

```bash
festin scan -w -f domains.txt -dr '.example\.com.'
# in another terminal:
echo "new-target.example.com" >> domains.txt
```

### Crawl and DNS options

| Flag | Effect |
| --- | --- |
| `-T, --http-timeout` | HTTP timeout in seconds (default 5). |
| `-M, --http-max-recursion` | Crawl depth limit (default 3). |
| `--no-links` | Disable the HTTP crawler. |
| `-dn, --no-dnsdiscover` | Disable CNAME following. |
| `-ds, --dns-resolver` | Custom DNS servers, comma separated. |

```bash
festin scan -T 20 -M 8 -ds 8.8.8.8 mydomain.com
```

### Black and white lists

`-B` skips domains containing any listed word; `-W` only analyzes domains
present in the list. They are mutually exclusive and the files must exist:

```bash
echo "cdn" > blacklist.txt
echo "photos" >> blacklist.txt
festin scan -q -B blacklist.txt -dr '.mydomain\.com.' mydomain.com
```

The regex in `-dr` must be valid: `.mydomain\.com.` matches, `mydomain.com` does not.

### Results files

| Flag | Content |
| --- | --- |
| `-rr, --result-file` | One JSON per line: origin domain, bucket name, object list. |
| `-rd, --discovered-domains` | Discovered domains after filters (one per line). |
| `-ra, --raw-discovered-domains` | Every discovered domain, unfiltered. |

```bash
festin scan -q mydomain.com -rd domains.txt
nmap -Pn -A -iL domains.txt -oN nmap-domains.txt
```

### Permutations and wordlists

`--permute` expands each domain into candidate bucket names (hyphenated,
underscored, TLD-less and every environment suffix such as `-prod`, `-backup`,
`-dev`...). `--wordlist` merges a wordlist file (one word per line, `#`
comments allowed) into the candidate set. Candidates are probed as bucket
names against the multi-cloud providers:

```bash
festin scan -q --permute example.com
festin scan -q --permute --wordlist wordlist.txt example.com
```

The candidate set is capped at 5000 names per scan (`MAX_CANDIDATES`).

### Multi-cloud probing

`--cloud` probes the plain domain-derived candidates (without the full
permutation set) against every supported provider — AWS S3, Azure Blob,
Google Cloud Storage, DigitalOcean Spaces and Backblaze B2:

```bash
festin scan -q --cloud example.com
```

Combine with `--permute` to probe permutations across all clouds.

### Secrets detection

`--secrets` downloads text objects (up to 10 MB each, 5 concurrent fetches)
from every bucket found and scans them for credentials: AWS access/secret
keys, Google API keys, Azure keys and connection strings, private keys, JWTs,
GitHub/Slack tokens and high-entropy assignments. Findings are printed grouped
by severity, critical first, and included in the scan result and exports:

```bash
festin scan -q --secrets --export sarif -o findings.sarif example.com
```

Matches are always redacted; the raw secret is never printed or stored.

### Monitoring with state and diff

`--state` persists each scan (identified by `--scan-id`, auto-generated when
omitted) to a JSON file. `--diff` compares the finished scan against the
latest scan that finished before it started:

```bash
festin scan -q --state state.json example.com        # first scan
festin scan -q --state state.json --diff example.com # prints what changed
```

The diff report lists new and removed buckets, per-bucket object changes and
new findings. This pairs well with cron:

```bash
# crontab: diff every night against the previous night
0 2 * * * festin scan -q --state /var/lib/festin/state.json --diff example.com
```

### Checkpoint and resume

Long scans can be interrupted safely. `--checkpoint` persists progress after
every domain; `--resume` skips domains already processed and reuses their
results from the checkpoint file:

```bash
festin scan -q --checkpoint scan.cp -f big-list.txt     # Ctrl-C whenever
festin scan -q --checkpoint scan.cp --resume -f big-list.txt
```

### Rate profiles

| Profile | Concurrency | Rate (req/s) | Jitter |
| --- | --- | --- | --- |
| `fast` | 20 | 50 | no |
| `deep` | 10 | 10 | no |
| `stealth` | 2 | 1 | yes |

```bash
festin scan -q --profile stealth example.com
```

The built-in adaptive controller backs off when providers answer 429, 403 or
5xx and recovers gradually afterwards.

### Exports

`--export` writes the scan result in a machine-readable format to `--output`:

| Format | Content |
| --- | --- |
| `csv` | One row per finding: bucket, object, rule, severity, line. |
| `sarif` | SARIF 2.1.0 document of all findings (CI/SARIF-aware tooling). |
| `jsonl` | One JSON object per line: buckets first, then findings. |

```bash
festin scan -q --export csv -o findings.csv example.com
festin scan -q --export jsonl -o scan.jsonl example.com
```

### Tor

With `--tor` all probes go through a Tor SOCKS5 proxy on `127.0.0.1:9050`
(TLS verification is relaxed because exit nodes break most certificates):

```bash
tor &
festin scan --tor mydomain.com
```

## REST API

`festin serve` exposes stored scan state over HTTP. Scan *execution* through
the API is intentionally disabled (POST to `/api/v1/scans` answers 503);
point `--state` at a state file written by `festin scan` and all read
endpoints work.

```bash
festin scan -q --state state.json example.com
festin serve --host 0.0.0.0 --port 8000 --state state.json
```

### Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v1/health` | Liveness probe. |
| POST | `/api/v1/scans` | Accepts `{"domains": [...]}`; returns 503 (no scan callback). |
| GET | `/api/v1/scans` | List scans, newest first. |
| GET | `/api/v1/scans/{scan_id}` | Full scan result by id. |
| GET | `/api/v1/findings` | All findings; optional `?severity=critical`. |
| GET | `/api/v1/buckets` | All buckets with their scan id. |

### curl examples

```bash
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/scans
curl http://localhost:8000/api/v1/scans/<scan_id>
curl "http://localhost:8000/api/v1/findings?severity=critical"
```

### Python example

```python
import httpx

with httpx.Client(base_url="http://localhost:8000") as client:
    health = client.get("/api/v1/health").json()
    print(health)  # {'status': 'ok', 'version': '0.1.0'}

    scans = client.get("/api/v1/scans").json()
    latest = scans["scans"][0]["scan_id"]

    detail = client.get(f"/api/v1/scans/{latest}").json()
    for bucket in detail["buckets"]:
        print(bucket["bucket_name"], len(bucket["objects"]))

    critical = client.get("/api/v1/findings", params={"severity": "critical"}).json()
    for finding in critical["findings"]:
        print(finding["rule_id"], finding["bucket_name"], finding["match"])
```

## CLI reference

### `festin scan`

```text
Usage: festin scan [OPTIONS] [DOMAINS]...

Run the bucket discovery scan over the given domains.

Arguments:
  domains  One or more start domains.

Options:
  -f, --file-domains PATH          File with one domain per line.
  -w, --watch                      Keep running and watch the '-f' domains file.
  -c, --concurrency INT RANGE      Maximum concurrent probes. [default: 5]
  --no-links                       Disable the HTTP link crawler.
  -T, --http-timeout FLOAT RANGE   HTTP connection timeout in seconds. [default: 5]
  -M, --http-max-recursion INT     Maximum crawl recursion. [default: 3]
  -dr, --domain-regex TEXT         Only follow domains matching this regex.
  -B, --domain-black-list PATH     File with blacklisted words.
  -W, --domain-white-list PATH     File with white-listed words.
  -rr, --result-file PATH          Streaming results file (one JSON per bucket).
  -rd, --discovered-domains PATH   Discovered domains after filters.
  -ra, --raw-discovered-domains    Every discovered domain, without filters.
  --tor                            Route traffic through Tor SOCKS5 (127.0.0.1:9050).
  --debug                          Enable debug output.
  --no-print                       Do not print results to the screen.
  -q, --quiet                      Quiet mode: suppress banner and progress output.
  -dn, --no-dnsdiscover            Do not follow DNS CNAMEs.
  -ds, --dns-resolver TEXT         Comma-separated custom DNS servers.
  --profile [deep|fast|stealth]    Rate profile preset; overrides concurrency/rate.
  --permute                        Expand domains into bucket-name permutations.
  --wordlist PATH                  Wordlist merged into the permutation set.
  --cloud                          Probe plain domain candidates across all clouds.
  --scan-id TEXT                   Identifier for this scan (default: auto-generated).
  --state PATH                     State file where the scan result is persisted.
  --diff                           Diff against the previous scan (requires --state).
  --secrets                        Download objects and scan them for secrets.
  --checkpoint PATH                Checkpoint file for resumable scans.
  --resume                         Resume a checkpointed scan (requires --checkpoint).
  --export [csv|sarif|jsonl]       Export format (requires --output).
  -o, --output PATH                Output path for --export.
```

Bare invocations route to scan: `festin example.com` is `festin scan example.com`.

### `festin serve`

```text
Usage: festin serve [OPTIONS]

Start the FestIn REST API server.

Endpoints live under /api/v1 (scans, findings, buckets, health). Scan
execution through the API is disabled: POST /api/v1/scans returns 503;
state endpoints work with --state.

Options:
  --host TEXT     Bind address. [default: 127.0.0.1]
  --port INT      Bind port (0 = ephemeral). [default: 8000]
  --state PATH    State file exposing stored scans over the API.
```

### `festin version`

Prints the installed version, e.g. `version: 0.1.0`.

## Development

```bash
uv sync                                  # runtime + dev dependencies
uv run pytest                            # full suite (~270 tests)
uv run ruff check festin tests           # lint
uv run ruff format festin tests          # format
uv run radon cc festin -n C              # complexity gate: prints nothing = pass
uv build                                 # sdist + wheel
```

Cyclomatic complexity is capped at 20 per function (ruff `mccabe` and the
`test_complexity.py` suite enforce it). Tests run without network access;
the end-to-end suite spins a local HTTP server.

## FAQ

**Does FestIn need AWS credentials?**
No. All probes use anonymous, read-only requests to public listing endpoints.

**Is FestIn destructive?**
No. It only fetches public listings and object content when `--secrets` is set;
it never writes, lists private data or enumerates objects beyond public listings.

**Why does my scan find nothing?**
Public listings must be enabled on the bucket (S3 `ListObjects` for anonymous
users). Buckets with listing disabled are invisible to any public probe.

**The crawler follows too many domains.**
Set `-dr` (domain regex), `-M` (recursion limit) or `-B`/`-W` lists. Without
`-dr` FestIn warns loudly because it will follow any link it sees.

**Difference between `--cloud`, `--permute` and the default S3 probe?**
The default probe tests the domain itself against AWS path-style/virtual-hosted
URLs. `--cloud` tests plain domain-derived candidate names against five
providers. `--permute` adds the full permutation set (and optionally a
wordlist) to the candidate names, which are then probed across all clouds.

**Does `festin serve` run scans through the API?**
Not currently. POST `/api/v1/scans` returns 503; the API serves state written
by `festin scan --state`. See the REST API section.

**Can I run scans from CI?**
Yes — use `--state` + `--diff` + `--export sarif`. A non-empty diff or new
critical findings is a good failure signal for the pipeline.

## License

BSD-3-Clause. See [LICENSE](LICENSE).