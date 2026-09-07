"""FestIn service authentication: JWT tokens + bcrypt passwords."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import web

try:
    import bcrypt
except ImportError:
    bcrypt = None  # type: ignore

try:
    from jose import JWTError, jwt
except ImportError:
    jwt = None  # type: ignore
    JWTError = Exception  # type: ignore


SECRET_DEFAULT = "festin-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


class PasswordHasher:
    """Sync bcrypt hashing — fast enough for async context."""

    def __init__(self) -> None:
        if bcrypt is None:
            raise ImportError("bcrypt not installed. pip install bcrypt")

    def hash_password(self, password: str) -> str:
        pw_bytes = password.encode()[:72]
        return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode()

    def verify_password(self, password: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode()[:72], hashed.encode())
        except ValueError:
            return False


class TokenService:
    """JWT access token creation and verification."""

    def __init__(self, secret_key: str = SECRET_DEFAULT, expires_minutes: int = 60) -> None:
        if jwt is None:
            raise ImportError("python-jose[cryptography] not installed")
        self._secret_key = secret_key
        self._algorithm = ALGORITHM
        self._expires_minutes = expires_minutes

    def create_token(self, username: str) -> str:
        expire = datetime.now(UTC) + timedelta(minutes=self._expires_minutes)
        payload = {
            "sub": username,
            "exp": expire.timestamp(),
            "iat": datetime.now(UTC).timestamp(),
        }
        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    def verify_token(self, token: str) -> str | None:
        try:
            payload = jwt.decode(token, self._secret_key, [self._algorithm])
            return payload.get("sub")
        except Exception:
            return None


class AuthService:
    """Service layer combining user management + auth."""

    def __init__(self, database: Any, secret_key: str = SECRET_DEFAULT) -> None:
        self._db = database
        self._hasher = PasswordHasher()
        self._token_service = TokenService(secret_key=secret_key)

    async def init_admin(self, username: str = "admin", password: str | None = None) -> bool:
        if await self._db.user_exists():
            return False
        pw = password or secrets.token_urlsafe(12)
        pw_hash = self._hasher.hash_password(pw)
        await self._db.create_user(username, pw_hash, "admin")
        print(f"[auth] Admin: {username} (password: {pw})")
        return True

    async def login(self, username: str, password: str) -> dict[str, Any]:
        user = await self._db.get_user_by_username(username)
        if user is None:
            raise ValueError("Invalid credentials")
        if not self._hasher.verify_password(password, user["password_hash"]):
            raise ValueError("Invalid credentials")
        token = self._token_service.create_token(username)
        return {
            "access_token": token,
            "token_type": "bearer",
            "username": username,
            "role": user.get("role", "viewer"),
        }

    async def register(self, username: str, password: str, role: str = "viewer") -> dict[str, Any]:
        """Register a new user. Returns its public representation."""
        if not username or not password:
            raise ValueError("username and password are required")
        existing = await self._db.get_user_by_username(username)
        if existing is not None:
            raise ValueError(f"User '{username}' already exists")
        pw_hash = self._hasher.hash_password(password)
        user_id = await self._db.create_user(username, pw_hash, role)
        return {"id": user_id, "username": username, "role": role}

    async def verify(self, token: str) -> dict[str, Any] | None:
        username = self._token_service.verify_token(token)
        if username is None:
            return None
        user = await self._db.get_user_by_username(username)
        if user is None:
            return None
        return {"user_id": user["id"], "username": username, "role": user.get("role", "viewer")}

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        row = await self._db.get_user(user_id)
        if row is None:
            return None
        return {"id": row["id"], "username": row["username"], "role": row["role"]}


class AuthMiddleware:
    """aiohttp middleware for optional basic auth from a users file.

    Users file format (one per line): ``username:password``. When no file
    is provided, the middleware allows all requests (open mode).
    """

    def __init__(self, users: dict[str, str] | None = None) -> None:
        self._users = users or {}
        self._hasher = PasswordHasher()

    @classmethod
    def from_file(cls, path: Any) -> AuthMiddleware:
        """Build middleware from a ``username:password`` lines file."""
        users: dict[str, str] = {}
        try:
            with open(path) as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    username, _, password = line.partition(":")
                    if username and password:
                        users[username] = password
        except OSError:
            pass
        return cls(users)

    @web.middleware
    async def __call__(self, request: web.Request, handler: Any) -> web.StreamResponse:
        """Check basic auth credentials against the users dict."""
        if not self._users:
            return await handler(request)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "Basic"})
        import base64

        try:
            decoded = base64.b64decode(auth_header[6:]).decode()
            username, _, password = decoded.partition(":")
        except Exception:
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "Basic"}) from None
        expected = self._users.get(username)
        if expected is None or not secrets.compare_digest(password, expected):
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "Basic"})
        return await handler(request)


class JWTMiddleware:
    """Bearer-JWT auth middleware.

    Verifies the ``Authorization: Bearer <jwt>`` header on every request
    except those whose path is in ``exempt_paths``. On success stores the
    authenticated username in ``request["user"]``.
    """

    # aiohttp's new-style middleware protocol: the version marker must be
    # visible on the instance, not only on the decorated __call__ function.
    __middleware_version__ = 1

    # NOTE: /api/v1/auth/register is intentionally NOT exempt: the handler
    # needs request["user"] to tell an admin apart when users already exist.
    # Anonymous first-user bootstrap still works because the middleware
    # passes requests without an Authorization header through to the
    # register handler, which checks user_count there.
    DEFAULT_EXEMPT_PATHS = frozenset(
        {
            "/api/v1/auth/login",
            "/api/v1/health",
        }
    )

    def __init__(
        self,
        token_service: TokenService | None = None,
        exempt_paths: set[str] | None = None,
    ) -> None:
        self._tokens = token_service or TokenService()
        self._exempt_paths = (
            exempt_paths if exempt_paths is not None else set(self.DEFAULT_EXEMPT_PATHS)
        )

    @classmethod
    def from_secret(
        cls,
        secret: str,
        exempt_paths: set[str] | None = None,
    ) -> JWTMiddleware:
        """Build the middleware from a shared secret."""
        return cls(TokenService(secret_key=secret), exempt_paths=exempt_paths)

    @web.middleware
    async def __call__(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if request.path in self._exempt_paths:
            return await handler(request)

        auth_header = request.headers.get("Authorization", "")
        if request.path == "/api/v1/auth/register" and not auth_header.startswith("Bearer "):
            # First-user bootstrap: let the register handler decide.
            return await handler(request)
        if not auth_header.startswith("Bearer "):
            raise web.HTTPUnauthorized(
                text='{"error": "unauthorized"}',
                content_type="application/json",
            )
        token = auth_header[len("Bearer ") :].strip()
        username = self._tokens.verify_token(token)
        if username is None:
            raise web.HTTPUnauthorized(
                text='{"error": "unauthorized"}',
                content_type="application/json",
            )
        request["user"] = username
        return await handler(request)
