# Register matrix + role-guard tests for the auth flow.
# Covers the frozen auth contract documented in docs/usage/api.md.
from __future__ import annotations

import pytest


@pytest.fixture
async def app_client(aiohttp_client):
    """Full aiohttp app with JWT auth, isolated DB per test."""
    import tempfile
    from pathlib import Path

    from festin.service.serve import ServiceConfig, create_app

    with tempfile.TemporaryDirectory() as tmp:
        config = ServiceConfig(db_path=Path(tmp) / "auth.db")
        app = await create_app(config)
        client = await aiohttp_client(app)
        yield client


async def _register(client, username: str, password: str, token: str | None = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
        headers=headers,
    )


async def _login(client, username: str, password: str):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status == 200, await resp.text()
    return (await resp.json())["access_token"]


class TestRegisterMatrix:
    async def test_first_register_creates_admin(self, app_client):
        resp = await _register(app_client, "admin", "strong-pass-1")
        assert resp.status == 201
        data = await resp.json()
        assert data["username"] == "admin"

    async def test_anon_on_populated_db_is_401(self, app_client):
        await _register(app_client, "admin", "strong-pass-1")
        resp = await _register(app_client, "eve", "whatever")
        assert resp.status == 401

    async def test_admin_can_register_viewer(self, app_client):
        await _register(app_client, "admin", "strong-pass-1")
        token = await _login(app_client, "admin", "strong-pass-1")
        resp = await _register(app_client, "bob", "bob-pass", token=token)
        assert resp.status == 201
        data = await resp.json()
        assert data["username"] == "bob"

    async def test_viewer_cannot_register(self, app_client):
        await _register(app_client, "admin", "strong-pass-1")
        admin_token = await _login(app_client, "admin", "strong-pass-1")
        await _register(app_client, "bob", "bob-pass", token=admin_token)
        bob_token = await _login(app_client, "bob", "bob-pass")
        resp = await _register(app_client, "eve", "eve-pass", token=bob_token)
        assert resp.status == 403

    async def test_duplicate_username_rejected(self, app_client):
        await _register(app_client, "admin", "strong-pass-1")
        token = await _login(app_client, "admin", "strong-pass-1")
        resp = await _register(app_client, "bob", "pass-1", token=token)
        assert resp.status == 201
        resp2 = await _register(app_client, "bob", "pass-2", token=token)
        assert resp2.status in (400, 409)


class TestLoginFlow:
    async def test_wrong_password_401(self, app_client):
        await _register(app_client, "admin", "right-pass")
        resp = await app_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong-pass"},
        )
        assert resp.status == 401

    async def test_unknown_user_401(self, app_client):
        resp = await app_client.post(
            "/api/v1/auth/login",
            json={"username": "ghost", "password": "x"},
        )
        assert resp.status == 401

    async def test_valid_login_returns_token_and_role(self, app_client):
        await _register(app_client, "admin", "right-pass")
        resp = await app_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "right-pass"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["access_token"]
        assert data["role"] == "admin"
        assert data["username"] == "admin"

    async def test_health_is_public(self, app_client):
        resp = await app_client.get("/api/v1/health")
        assert resp.status == 200

    async def test_protected_endpoint_requires_token(self, app_client):
        resp = await app_client.get("/api/v1/projects")
        assert resp.status == 401
