# Scanner CLI reference

`festin scan` — the bucket discovery engine. Every option, with an example.

## Synopsis

```bash
festin scan [OPTIONS] [DOMAINS]...
```

## Arguments

| Argument | Description |
|---|---|
| `domains` | One or more start domains, e.g. `example.com`. |

## Options

### Input

| Flag | Short | Description |
|---|---|---|
| `--file-domains <path>` | `-f` | File with one domain per line |
| `--watch` | `-w` | Keep running; new lines in the `-f` file are scanned as they appear |

### Discovery

| Flag | Short | Default | Description |
|---|---|---|---|
| `--permute` | | off | Expand each domain into bucket-name permutations (`example.com` → `example-com`, `com-example`, `examplebackup`, …) and probe them |
| `--wordlist <path>` | | | Extra words merged into the permutation set |
| `--cloud` | | off | Probe plain domain-derived names across **all** supported cloud providers (AWS, GCP, Azure) |
| `--no-dnsdiscover` | `-dn` | off | Don't follow DNS CNAMEs to storage |
| `--dns-resolver <servers>` | `-ds` | system | Comma-separated custom resolvers, e.g. `8.8.8.8,1.1.1.1` |

### Crawling

| Flag | Short | Default | Description |
|---|---|---|---|
| `--no-links` | | off | Disable the HTTP link crawler (probe only the entry domain) |
| `--http-max-recursion <n>` | `-M` | 3 | Crawl depth when following links |
| `--http-timeout <s>` | `-T` | 5 | HTTP connect timeout in seconds |
| `--domain-regex <re>` | `-dr` | none | Only follow links whose domain matches this regex |
| `--domain-black-list <path>` | `-B` | none | File of blacklisted domain words |
| `--domain-white-list <path>` | `-W` | none | File of whitelisted domain words |

### Rate control

| Flag | Short | Default | Description |
|---|---|---|---|
| `--concurrency <n>` | `-c` | 5 | Max concurrent probes |
| `--profile <name>` | | none | Preset `deep`, `fast` or `stealth`; overrides concurrency/rate |
| `--tor` | | off | Route traffic through a local Tor SOCKS5 proxy (`127.0.0.1:9050`) |

### Output

| Flag | Short | Description |
|---|---|---|
| `--result-file <path>` | `-rr` | Streaming results file — one JSON object per bucket |
| `--export csv\|sarif\|jsonl` | | Report format (requires `--output`) |
| `--output <path>` | `-o` | Output path for `--export` |
| `--discovered-domains <path>` | `-rd` | Discovered domains after filters |
| `--raw-discovered-domains <path>` | `-ra` | Every discovered domain, unfiltered |
| `--no-print` | | Don't print results to stdout |
| `--quiet` | `-q` | Suppress banner and progress |

### Resilience

| Flag | Description |
|---|---|
| `--checkpoint <path>` | Persist scan progress; enables resume |
| `--resume` | Resume a checkpointed scan (requires `--checkpoint`) |
| `--scan-id <str>` | Identifier for this scan (default: auto-generated) |
| `--state <path>` | State file persisted after the scan (enables diffing) |
| `--debug` | Debug output |

## Worked examples

### Perimeter audit of one company

```bash
festin scan acme.com \
  --permute --cloud \
  --http-max-recursion 4 \
  --export sarif --output acme.sarif \
  --result-file acme-buckets.jsonl
```

Probes ~thousands of permutations across AWS/GCP/Azure, crawls the site 4 levels deep, and produces both a SARIF report (for GitHub code scanning) and a streaming bucket log.

### Continuous bug-bounty recon

```bash
# terminal 1: watch a target file
festin scan -f targets.txt --watch --checkpoint recon.ckpt --quiet
```

Append new scopes to `targets.txt` at any time; they're picked up automatically. If the process dies, `--resume` picks up where it left off.

### Tor-routed stealth scan

```bash
# assumes tor is running with default SOCKS5 on 9050
festin scan sensitive-target.com --tor --profile stealth --quiet
```

### DNS-focused discovery (skip crawling)

```bash
festin scan example.com --no-links --dns-resolver 8.8.8.8,9.9.9.9 --cloud
```

Fast: only DNS + cloud probing, no HTTP crawl.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Scan finished (findings or not) |
| 1 | Fatal error (bad arguments, network failure at startup) |
| 130 | Interrupted (checkpoint kept if `--checkpoint` was set) |