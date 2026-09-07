"""Pydantic models shared across service, API and scheduler packages."""

from __future__ import annotations

from enum import StrEnum

# ── Enums ────────────────────────────────────────────────────────────────


class DomainStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class ScanStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Domain ───────────────────────────────────────────────────────────────


class DomainCreate:
    domain: str
    enabled: bool
    scan_interval_seconds: int
    cloud: bool
    permute: bool
    secrets: bool
    state_file: str | None

    def __init__(
        self,
        domain: str = "example.com",
        enabled: bool = True,
        scan_interval_seconds: int = 300,
        cloud: bool = False,
        permute: bool = False,
        secrets: bool = False,
        state_file: str | None = None,
    ) -> None:
        self.domain = domain
        self.enabled = enabled
        self.scan_interval_seconds = scan_interval_seconds
        self.cloud = cloud
        self.permute = permute
        self.secrets = secrets
        self.state_file = state_file


class DomainUpdate:
    enabled: bool | None
    scan_interval_seconds: int | None
    cloud: bool | None
    permute: bool | None
    secrets: bool | None
    state_file: str | None

    def __init__(
        self,
        enabled: bool | None = None,
        scan_interval_seconds: int | None = None,
        cloud: bool | None = None,
        permute: bool | None = None,
        secrets: bool | None = None,
        state_file: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.scan_interval_seconds = scan_interval_seconds
        self.cloud = cloud
        self.permute = permute
        self.secrets = secrets
        self.state_file = state_file


class DomainRead:
    id: int
    domain: str
    enabled: bool
    scan_interval_seconds: int
    cloud: bool
    permute: bool
    secrets: bool
    state_file: str | None
    last_scan_id: str | None
    last_scan_status: str | None
    last_scanned_at: str | None
    created_at: str
    updated_at: str

    def __init__(
        self,
        id: int,
        domain: str,
        enabled: bool,
        scan_interval_seconds: int,
        cloud: bool,
        permute: bool,
        secrets: bool,
        state_file: str | None,
        last_scan_id: str | None = None,
        last_scan_status: str | None = None,
        last_scanned_at: str | None = None,
        created_at: str = "",
        updated_at: str = "",
    ) -> None:
        self.id = id
        self.domain = domain
        self.enabled = enabled
        self.scan_interval_seconds = scan_interval_seconds
        self.cloud = cloud
        self.permute = permute
        self.secrets = secrets
        self.state_file = state_file
        self.last_scan_id = last_scan_id
        self.last_scan_status = last_scan_status
        self.last_scanned_at = last_scanned_at
        self.created_at = created_at
        self.updated_at = updated_at


# ── Scan ─────────────────────────────────────────────────────────────────


class ScanRead:
    id: int
    domain_id: int
    status: str
    scan_id: str
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    domains_scanned: list[str]
    buckets_found: int
    findings_count: int
    diff_data: dict | None

    def __init__(
        self,
        id: int,
        domain_id: int,
        status: str,
        scan_id: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        duration_seconds: float | None = None,
        domains_scanned: list[str] | None = None,
        buckets_found: int = 0,
        findings_count: int = 0,
        diff_data: dict | None = None,
    ) -> None:
        self.id = id
        self.domain_id = domain_id
        self.status = status
        self.scan_id = scan_id
        self.started_at = started_at
        self.finished_at = finished_at
        self.duration_seconds = duration_seconds
        self.domains_scanned = domains_scanned or []
        self.buckets_found = buckets_found
        self.findings_count = findings_count
        self.diff_data = diff_data


# ── Auth ─────────────────────────────────────────────────────────────────


class LoginRequest:
    username: str
    password: str

    def __init__(self, username: str = "", password: str = "") -> None:
        self.username = username
        self.password = password


class TokenResponse:
    access_token: str
    token_type: str
    expires_at: str

    def __init__(self, access_token: str = "", expires_at: str = "") -> None:
        self.access_token = access_token
        self.token_type = "bearer"
        self.expires_at = expires_at


class UserRead:
    id: int
    username: str

    def __init__(self, id: int = 0, username: str = "") -> None:
        self.id = id
        self.username = username


# ── Dashboard Aggregates ─────────────────────────────────────────────────


class DomainStats:
    domain: str
    total_scans: int
    last_status: str | None
    buckets_total: int
    critical_findings: int
    last_scanned_at: str | None

    def __init__(
        self,
        domain: str = "",
        total_scans: int = 0,
        last_status: str | None = None,
        buckets_total: int = 0,
        critical_findings: int = 0,
        last_scanned_at: str | None = None,
    ) -> None:
        self.domain = domain
        self.total_scans = total_scans
        self.last_status = last_status
        self.buckets_total = buckets_total
        self.critical_findings = critical_findings
        self.last_scanned_at = last_scanned_at


class DashboardOverview:
    total_domains: int
    active_domains: int
    total_scans: int
    total_buckets_found: int
    total_findings: int
    critical_findings: int
    domains: list[DomainStats]

    def __init__(
        self,
        total_domains: int = 0,
        active_domains: int = 0,
        total_scans: int = 0,
        total_buckets_found: int = 0,
        total_findings: int = 0,
        critical_findings: int = 0,
        domains: list[DomainStats] | None = None,
    ) -> None:
        self.total_domains = total_domains
        self.active_domains = active_domains
        self.total_scans = total_scans
        self.total_buckets_found = total_buckets_found
        self.total_findings = total_findings
        self.critical_findings = critical_findings
        self.domains = domains or []
