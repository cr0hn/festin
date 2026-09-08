# Security hardening

FestIn finds exposure — don't let the dashboard become one.

## Threat model

| Asset | Threat |
|---|---|
| Dashboard (findings, secrets in redacted matches) | unauthorized access, token theft |
| JWT secret | forgery of admin tokens |
| `/auth/login` | brute force (no rate limit in-app) |
| Scanner | scanning targets you don't own (legal exposure) |
| SQLite file | contains all findings — treat as sensitive |

## Mandatory checklist

- [ ] **Set `FESTIN_JWT_SECRET`** to a random value. The dev fallback is public knowledge; with it, anyone can mint admin tokens:

    ```bash
    python3 -c "import secrets; print(secrets.token_urlsafe(32))"
    kubectl create secret generic festin-secrets --from-literal=jwt-secret="$SECRET"
    ```

- [ ] **Delete the demo database** (`rm data/festin.db`) — the checked-in one has known credentials (`admin/admin123`).
- [ ] **Strong bootstrap password.** The first registered user becomes admin; register it yourself before exposing the service.
- [ ] **TLS everywhere.** Terminate at your proxy/ingress; the service speaks plain HTTP ([docker](docker.md#reverse-proxy-tls)).
- [ ] **Second-layer rate limit at the proxy.** Login/register are limited in-app (5 attempts / 60 s per IP, env-tunable — see [configuration](../usage/configuration.md)); a proxy limit is still worth it as defense in depth:

    ```nginx
    # nginx: 5 req/min per IP on login
    limit_req_zone $binary_remote_addr zone=login:1m rate=5r/m;
    location /api/v1/auth/login { limit_req zone=login burst=3 nodelay; proxy_pass http://127.0.0.1:8420; }
    ```

- [ ] **Bind to `127.0.0.1` or a private network** when not behind a proxy. `0.0.0.0` only inside containers.
- [ ] **Treat findings as secrets.** The API redacts matches (`AKIA****EXAMPLE`) but rule names, buckets and object paths leak structure. Limit dashboard access like you limit your SIEM.
- [ ] **Back up with `.backup`,** not `cp` ([docker](docker.md#data-lifecycle)).
??? note "Dependabot alert: python-ecdsa (Minerva timing attack)"
    `ecdsa` is a transitive dependency of `python-jose` (JWT support).
    FestIn only uses **HS256** (symmetric HMAC) — module-level import
    tracing confirms `ecdsa` is **never imported** at runtime, so the
    P-256 timing attack does not apply. The alert is dismissed with this
    justification. If you ever switch to asymmetric algorithms (ES256),
    replace `python-jose` with PyJWT and re-evaluate.

## Auth model — know its edges

| Fact | Consequence |
|---|---|
| JWT HS256, 60 min, no refresh/revocation | a stolen token is valid ≤ 60 min; shortening expiry = more re-logins |
| Token lives in `localStorage` | XSS in any injected script = token theft; don't inject scripts |
| No account lockout | proxy rate-limit is the mitigation |
| Viewer role is read-only by API *and* UI | mutations are admin-guarded server-side (`_admin_guard`) |
| Bootstrap: first register on empty DB = admin | expose the service publicly *after* bootstrapping, or keep an allowlist until then |

## Scanner legality

FestIn probes third-party infrastructure (DNS, HTTP, cloud storage endpoints).

- Only scan **assets you own or are authorized to test** (bug-bounty scope, contracts).
- `--tor` anonymizes probes but doesn't make unauthorized scanning legal.
- Respect rate limits: `--profile stealth` exists for a reason; a `--concurrency 50` run looks like an attack.

## Runtime isolation

```bash
# container with no network surprises: read-only root, drop capabilities
docker run -d --name festin \
  --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 8420:8420 -v festin-data:/data \
  -e FESTIN_JWT_SECRET=$SECRET \
  festin:local
```

In Kubernetes: the manifests on the [Kubernetes page](kubernetes.md) already set `runAsNonRoot`; add a `NetworkPolicy` if the dashboard should not reach arbitrary targets (it must reach DNS + HTTP + cloud storage endpoints for scanning).

## Reporting a vulnerability

Open a private advisory via [GitHub Security Advisories](https://github.com/cr0hn/festin/security/advisories/new) — not a public issue.